package com.gb4pc.mockgallery

import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner

/**
 * Regression guard for issue #230 (and #231 / #232, which shared this root cause).
 *
 * LastPhotoActivity used to embed "LIMIT 1" in the MediaStore query sort order. Since
 * API 29 the platform validates the ORDER BY argument and rejects an embedded LIMIT
 * with "IllegalArgumentException: Invalid token LIMIT". That crashed the activity in
 * onCreate the instant the gallery was opened, so every test that tapped the overlay
 * (empty or populated roll) saw the gallery die and the green camera get restored to
 * the foreground -- which is why test2a measured ~87% green when it expected an empty
 * gallery, and why the populated-roll tests never actually displayed the captured photo.
 *
 * The CI logcat for the failing run showed the crash directly:
 *   FATAL EXCEPTION: java.lang.IllegalArgumentException: Invalid token LIMIT
 *       at com.gb4pc.mockgallery.LastPhotoActivity.queryLastPhotoUri(LastPhotoActivity.kt)
 */
@RunWith(RobolectricTestRunner::class)
class LastPhotoActivityRobolectricTest {
    /**
     * The real guard: the sort order must not contain a LIMIT clause. Robolectric's
     * MediaStore shim does not reproduce the platform's ORDER BY validation, so a mere
     * "launch does not throw" test would pass even with the bug present. Asserting the
     * invariant on the actual string the activity uses catches the regression directly.
     */
    @Test
    fun `sort order has no LIMIT clause`() {
        assertFalse(
            "LastPhotoActivity's MediaStore sort order must not embed a LIMIT clause; " +
                "API 29+ rejects it with \"Invalid token LIMIT\" and crashes the activity " +
                "on launch (issue #230). Was: ${LastPhotoActivity.LAST_PHOTO_SORT_ORDER}",
            LastPhotoActivity.LAST_PHOTO_SORT_ORDER.contains("LIMIT", ignoreCase = true),
        )
    }

    /**
     * Secondary guard: launching against an empty MediaStore (the exact failing scenario)
     * must complete onCreate -> onResume without throwing.
     */
    @Test
    fun `launching against an empty MediaStore does not crash`() {
        val controller = Robolectric.buildActivity(LastPhotoActivity::class.java).setup()
        assertNotNull(controller.get())
        controller.pause().stop().destroy()
    }
}
