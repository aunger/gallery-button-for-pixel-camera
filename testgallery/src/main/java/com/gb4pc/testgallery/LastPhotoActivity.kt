package com.gb4pc.testgallery

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
        val sortOrder = "${MediaStore.Images.Media.DATE_ADDED} DESC LIMIT 1"
        contentResolver.query(
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
            projection,
            null,
            null,
            sortOrder
        )?.use { cursor ->
            if (cursor.moveToFirst()) {
                val id = cursor.getLong(cursor.getColumnIndexOrThrow(MediaStore.Images.Media._ID))
                return Uri.withAppendedPath(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, id.toString())
            }
        }
        return null
    }
}
