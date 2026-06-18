package com.gb4pc.mockgallery

import android.app.Activity
import android.net.Uri
import android.os.Bundle
import android.provider.MediaStore
import android.widget.ImageView

/**
 * Displays the most recently added photo from MediaStore.
 *
 * Empty state: the root view and ImageView both have a solid black background.
 * This ensures that a "no GREEN" assertion cannot be accidentally satisfied by
 * transparent or uninitialized pixels when the camera roll is empty.
 */
class LastPhotoActivity : Activity() {
    companion object {
        /**
         * Sort order for the most-recent-photo query: newest first.
         *
         * Do NOT append a "LIMIT" clause here. Since API 29 the platform validates the
         * ORDER BY argument and rejects an embedded LIMIT with
         * "IllegalArgumentException: Invalid token LIMIT", which crashed this activity on
         * launch (issue #230). The caller reads only the first row via moveToFirst(), so
         * no SQL-level LIMIT is needed. Exposed as a constant so a unit test can assert
         * the no-LIMIT invariant without depending on the platform's SQL validation
         * (which Robolectric does not reproduce).
         */
        internal const val LAST_PHOTO_SORT_ORDER = "${MediaStore.Images.Media.DATE_ADDED} DESC"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_last_photo)
        loadLastPhoto()
    }

    private fun loadLastPhoto() {
        val uri = queryLastPhotoUri() ?: return
        val photoView = findViewById<ImageView>(R.id.photo_view)
        photoView.setImageURI(uri)
    }

    private fun queryLastPhotoUri(): Uri? {
        val projection = arrayOf(MediaStore.Images.Media._ID)
        contentResolver
            .query(
                MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                projection,
                null,
                null,
                LAST_PHOTO_SORT_ORDER,
            )?.use { cursor ->
                if (cursor.moveToFirst()) {
                    val id = cursor.getLong(cursor.getColumnIndexOrThrow(MediaStore.Images.Media._ID))
                    return Uri.withAppendedPath(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, id.toString())
                }
            }
        return null
    }
}
