package com.gb4pc.viewer

import android.app.KeyguardManager
import android.content.ContentUris
import android.content.Intent
import android.database.ContentObserver
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.recyclerview.widget.ItemTouchHelper
import androidx.recyclerview.widget.RecyclerView
import androidx.viewpager2.widget.ViewPager2
import com.davemorrissey.labs.subscaleview.ImageSource
import com.davemorrissey.labs.subscaleview.SubsamplingScaleImageView
import com.gb4pc.R
import com.gb4pc.util.DebugLog
import com.google.android.material.snackbar.Snackbar

/**
 * Secure filmstrip viewer displayed on top of the lock screen (§5).
 * SF-06: Uses setShowWhenLocked and setTurnScreenOn.
 */
class SecureViewerActivity : ComponentActivity() {

    private lateinit var viewPager: ViewPager2
    private lateinit var emptyMessage: TextView
    private lateinit var shareButton: ImageView
    private lateinit var adapter: MediaPagerAdapter
    private val handler = Handler(Looper.getMainLooper())
    private var mediaObserver: ContentObserver? = null

    private val sessionTracker get() = SessionTracker.instance

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setShowWhenLocked(true)
        setTurnScreenOn(true)

        setupLayout()
        setupMediaObserver()
        refreshMedia()
    }

    override fun onResume() {
        super.onResume()
        // SF-15: If device unlocked while viewer is open, finish
        val km = getSystemService(KEYGUARD_SERVICE) as KeyguardManager
        if (!km.isKeyguardLocked) {
            finish()
            return
        }
        refreshMedia()
    }

    override fun onDestroy() {
        mediaObserver?.let { contentResolver.unregisterContentObserver(it) }
        super.onDestroy()
    }

    private fun setupLayout() {
        val root = ViewGroup.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT
        )

        val container = android.widget.FrameLayout(this).apply { layoutParams = root }
        container.setBackgroundColor(0xFF000000.toInt())

        // ViewPager2 for horizontal swiping (SF-07)
        viewPager = ViewPager2(this).apply {
            layoutParams = ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        }
        adapter = MediaPagerAdapter()
        viewPager.adapter = adapter
        container.addView(viewPager)

        // Empty state message (SF-12)
        emptyMessage = TextView(this).apply {
            text = getString(R.string.viewer_no_photos)
            setTextColor(0xFFFFFFFF.toInt())
            textSize = 18f
            gravity = android.view.Gravity.CENTER
            layoutParams = android.widget.FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        }
        container.addView(emptyMessage)

        // Share button (SF-11)
        shareButton = ImageView(this).apply {
            setImageResource(android.R.drawable.ic_menu_share)
            setColorFilter(0xFFFFFFFF.toInt())
            val size = (48 * resources.displayMetrics.density).toInt()
            val margin = (16 * resources.displayMetrics.density).toInt()
            layoutParams = android.widget.FrameLayout.LayoutParams(size, size).apply {
                gravity = android.view.Gravity.TOP or android.view.Gravity.END
                topMargin = margin
                marginEnd = margin
            }
            setPadding(
                (8 * resources.displayMetrics.density).toInt(),
                (8 * resources.displayMetrics.density).toInt(),
                (8 * resources.displayMetrics.density).toInt(),
                (8 * resources.displayMetrics.density).toInt()
            )
            setBackgroundResource(android.R.drawable.dialog_holo_dark_frame)
            setOnClickListener { handleShare() }
        }
        container.addView(shareButton)

        // Swipe-to-delete via ItemTouchHelper on the ViewPager2's RecyclerView (SF-10)
        setupSwipeToDelete()

        setContentView(container)
    }

    private fun setupSwipeToDelete() {
        val recyclerView = viewPager.getChildAt(0) as? RecyclerView ?: return
        val swipeCallback = object : ItemTouchHelper.SimpleCallback(0, ItemTouchHelper.UP or ItemTouchHelper.DOWN) {
            override fun onMove(rv: RecyclerView, vh: RecyclerView.ViewHolder, target: RecyclerView.ViewHolder) = false

            override fun onSwiped(viewHolder: RecyclerView.ViewHolder, direction: Int) {
                val position = viewHolder.adapterPosition
                val media = adapter.getItemAt(position) ?: return
                handleDelete(media, position)
            }
        }
        ItemTouchHelper(swipeCallback).attachToRecyclerView(recyclerView)
    }

    private fun handleDelete(media: MediaItem, position: Int) {
        // Remove from session immediately
        sessionTracker.removeMedia(media.uri)
        refreshMedia()

        // SF-10: Show undo snackbar
        val rootView = findViewById<View>(android.R.id.content)
        Snackbar.make(rootView, R.string.viewer_photo_deleted, Snackbar.LENGTH_LONG)
            .setDuration(com.gb4pc.Constants.UNDO_TIMEOUT_MS.toInt())
            .setAction(R.string.viewer_undo) {
                // Undo: re-add to session
                sessionTracker.addMedia(media)
                refreshMedia()
            }
            .addCallback(object : Snackbar.Callback() {
                override fun onDismissed(transientBottomBar: Snackbar?, event: Int) {
                    if (event != DISMISS_EVENT_ACTION) {
                        // Actually delete from MediaStore
                        deleteFromMediaStore(media.uri)
                    }
                }
            })
            .show()
    }

    private fun deleteFromMediaStore(uriString: String) {
        try {
            val uri = Uri.parse(uriString)
            contentResolver.delete(uri, null, null)
            DebugLog.log("Deleted media: $uriString")
        } catch (e: Exception) {
            DebugLog.log("Failed to delete media: ${e.message}")
        }
    }

    /**
     * SF-11: Share requires authentication first.
     */
    private fun handleShare() {
        val currentPosition = viewPager.currentItem
        val media = adapter.getItemAt(currentPosition) ?: return

        val km = getSystemService(KEYGUARD_SERVICE) as KeyguardManager
        km.requestDismissKeyguard(this, object : KeyguardManager.KeyguardDismissCallback() {
            override fun onDismissSucceeded() {
                val shareIntent = Intent(Intent.ACTION_SEND).apply {
                    type = if (media.isVideo) "video/*" else "image/*"
                    putExtra(Intent.EXTRA_STREAM, Uri.parse(media.uri))
                    addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                }
                startActivity(Intent.createChooser(shareIntent, null))
            }

            override fun onDismissError() {}
            override fun onDismissCancelled() {}
        })
    }

    /**
     * SF-03/SF-14: Observe MediaStore for new/removed media.
     */
    private fun setupMediaObserver() {
        mediaObserver = object : ContentObserver(handler) {
            override fun onChange(selfChange: Boolean, uri: Uri?) {
                queryNewMedia()
                refreshMedia()
            }
        }
        contentResolver.registerContentObserver(
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI, true, mediaObserver!!
        )
        contentResolver.registerContentObserver(
            MediaStore.Video.Media.EXTERNAL_CONTENT_URI, true, mediaObserver!!
        )
    }

    private fun queryNewMedia() {
        if (!sessionTracker.isSessionActive) return

        val projection = arrayOf(
            MediaStore.MediaColumns._ID,
            MediaStore.MediaColumns.DATE_ADDED,
            MediaStore.MediaColumns.RELATIVE_PATH,
            MediaStore.MediaColumns.MIME_TYPE
        )

        val threshold = (sessionTracker.sessionStartTimestamp / 1000) -
            (com.gb4pc.Constants.SESSION_TIMESTAMP_TOLERANCE_MS / 1000)
        val selection = "${MediaStore.MediaColumns.DATE_ADDED} >= ?"
        val selectionArgs = arrayOf(threshold.toString())

        // Query images
        queryMediaUri(
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
            projection, selection, selectionArgs, isVideo = false
        )
        // Query videos
        queryMediaUri(
            MediaStore.Video.Media.EXTERNAL_CONTENT_URI,
            projection, selection, selectionArgs, isVideo = true
        )
    }

    private fun queryMediaUri(
        contentUri: Uri,
        projection: Array<String>,
        selection: String,
        selectionArgs: Array<String>,
        isVideo: Boolean
    ) {
        contentResolver.query(contentUri, projection, selection, selectionArgs, null)?.use { cursor ->
            val idCol = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns._ID)
            val dateCol = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.DATE_ADDED)
            val pathCol = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.RELATIVE_PATH)

            while (cursor.moveToNext()) {
                val id = cursor.getLong(idCol)
                val dateAdded = cursor.getLong(dateCol) * 1000 // Convert to millis
                val relativePath = cursor.getString(pathCol) ?: ""
                val uri = ContentUris.withAppendedId(contentUri, id).toString()

                if (sessionTracker.isMediaInSession(dateAdded, relativePath)) {
                    // Check if already in session list
                    if (sessionTracker.getSessionMedia().none { it.uri == uri }) {
                        sessionTracker.addMedia(MediaItem(uri = uri, dateTaken = dateAdded, isVideo = isVideo))
                    }
                }
            }
        }
    }

    private fun refreshMedia() {
        val media = sessionTracker.getSessionMedia()
        adapter.submitList(media)
        emptyMessage.visibility = if (media.isEmpty()) View.VISIBLE else View.GONE
        viewPager.visibility = if (media.isEmpty()) View.GONE else View.VISIBLE
        shareButton.visibility = if (media.isEmpty()) View.GONE else View.VISIBLE
    }

    /**
     * ViewPager2 adapter for session media items.
     */
    inner class MediaPagerAdapter : RecyclerView.Adapter<MediaPagerAdapter.MediaViewHolder>() {

        private var items = listOf<MediaItem>()

        fun submitList(newItems: List<MediaItem>) {
            items = newItems
            notifyDataSetChanged()
        }

        fun getItemAt(position: Int): MediaItem? = items.getOrNull(position)

        override fun getItemCount() = items.size

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): MediaViewHolder {
            val container = FrameLayout(parent.context).apply {
                layoutParams = ViewGroup.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.MATCH_PARENT
                )
            }
            return MediaViewHolder(container)
        }

        override fun onBindViewHolder(holder: MediaViewHolder, position: Int) {
            val item = items[position]
            val container = holder.itemView as FrameLayout
            container.removeAllViews()

            try {
                val uri = Uri.parse(item.uri)
                if (item.isVideo) {
                    // SF-09: Show video thumbnail with play button overlay
                    val imageView = ImageView(container.context).apply {
                        layoutParams = FrameLayout.LayoutParams(
                            ViewGroup.LayoutParams.MATCH_PARENT,
                            ViewGroup.LayoutParams.MATCH_PARENT
                        )
                        scaleType = ImageView.ScaleType.FIT_CENTER
                    }
                    val bitmap = loadBitmap(uri)
                    imageView.setImageBitmap(bitmap)
                    container.addView(imageView)

                    // SF-09: Play button icon overlay
                    val playIcon = ImageView(container.context).apply {
                        setImageResource(android.R.drawable.ic_media_play)
                        setColorFilter(0xCCFFFFFF.toInt())
                        val iconSize = (64 * resources.displayMetrics.density).toInt()
                        layoutParams = FrameLayout.LayoutParams(iconSize, iconSize).apply {
                            gravity = android.view.Gravity.CENTER
                        }
                    }
                    container.addView(playIcon)

                    container.setOnClickListener {
                        Toast.makeText(this@SecureViewerActivity, R.string.viewer_unlock_to_play, Toast.LENGTH_SHORT).show()
                    }
                } else {
                    // SF-08: Use SubsamplingScaleImageView for pinch-to-zoom
                    val scaleView = SubsamplingScaleImageView(container.context).apply {
                        layoutParams = FrameLayout.LayoutParams(
                            ViewGroup.LayoutParams.MATCH_PARENT,
                            ViewGroup.LayoutParams.MATCH_PARENT
                        )
                        setMinimumDpi(80)
                        setDoubleTapZoomDpi(240)
                    }
                    scaleView.setImage(ImageSource.uri(uri))
                    container.addView(scaleView)
                }
            } catch (e: Exception) {
                DebugLog.log("Failed to load media: ${e.message}")
                val errorView = ImageView(container.context).apply {
                    layoutParams = FrameLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.MATCH_PARENT
                    )
                    setImageResource(android.R.drawable.ic_menu_report_image)
                    scaleType = ImageView.ScaleType.CENTER
                }
                container.addView(errorView)
            }
        }

        private fun loadBitmap(uri: Uri): android.graphics.Bitmap? {
            return if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.Q) {
                contentResolver.loadThumbnail(uri, android.util.Size(1024, 1024), null)
            } else {
                @Suppress("DEPRECATION")
                android.provider.MediaStore.Images.Media.getBitmap(contentResolver, uri)
            }
        }

        inner class MediaViewHolder(view: View) : RecyclerView.ViewHolder(view)
    }
}
