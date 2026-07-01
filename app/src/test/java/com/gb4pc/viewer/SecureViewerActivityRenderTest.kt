package com.gb4pc.viewer

import android.view.View
import android.widget.TextView
import androidx.test.core.app.ActivityScenario
import androidx.viewpager2.widget.ViewPager2
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf

/**
 * Robolectric regression test for the SecureViewerActivity race (#537).
 *
 * The viewer used to read SessionTracker exactly once in onCreate(). If the
 * session was still empty at that instant (population lands asynchronously, or
 * the session is briefly reset around tap time) the viewer showed the empty
 * "no photos" state forever. The reactive sessionMedia collector must re-render
 * when the session is populated after the activity has already started.
 */
@RunWith(RobolectricTestRunner::class)
class SecureViewerActivityRenderTest {
    private val tracker get() = SessionTracker.instance

    @Before
    fun resetSession() {
        // Start from a known, empty-but-active session on the process-wide singleton.
        tracker.startSession()
    }

    @After
    fun clearSession() {
        tracker.endSession()
    }

    private fun emptyMessageOf(activity: SecureViewerActivity): TextView {
        val root = activity.findViewById<View>(android.R.id.content)
        // The empty-state TextView is the only TextView added to the viewer layout.
        return findFirstTextView(root) ?: error("empty-state TextView not found")
    }

    private fun findFirstTextView(view: View): TextView? {
        if (view is TextView) return view
        if (view is android.view.ViewGroup) {
            for (i in 0 until view.childCount) {
                findFirstTextView(view.getChildAt(i))?.let { return it }
            }
        }
        return null
    }

    private fun viewPagerOf(activity: SecureViewerActivity): ViewPager2 {
        val root = activity.findViewById<View>(android.R.id.content)
        return findFirstViewPager(root) ?: error("ViewPager2 not found")
    }

    private fun findFirstViewPager(view: View): ViewPager2? {
        if (view is ViewPager2) return view
        if (view is android.view.ViewGroup) {
            for (i in 0 until view.childCount) {
                findFirstViewPager(view.getChildAt(i))?.let { return it }
            }
        }
        return null
    }

    @Test
    fun `viewer opened against empty session shows empty state`() {
        ActivityScenario.launch(SecureViewerActivity::class.java).use { scenario ->
            scenario.onActivity { activity ->
                shadowOf(activity.mainLooper).idle()
                assertEquals(View.VISIBLE, emptyMessageOf(activity).visibility)
                assertEquals(View.GONE, viewPagerOf(activity).visibility)
            }
        }
    }

    @Test
    fun `late population re-renders viewer from empty to populated`() {
        ActivityScenario.launch(SecureViewerActivity::class.java).use { scenario ->
            scenario.onActivity { activity ->
                shadowOf(activity.mainLooper).idle()
                // Regression precondition: viewer started with an empty session.
                assertEquals(View.VISIBLE, emptyMessageOf(activity).visibility)

                // Session is populated after the activity is already showing.
                tracker.addMedia(
                    MediaItem(uri = "content://media/1", dateTaken = 1000L, isVideo = false),
                )
                shadowOf(activity.mainLooper).idle()

                // The reactive collector must have re-rendered to the populated state.
                assertEquals(View.GONE, emptyMessageOf(activity).visibility)
                assertEquals(View.VISIBLE, viewPagerOf(activity).visibility)
            }
        }
    }
}
