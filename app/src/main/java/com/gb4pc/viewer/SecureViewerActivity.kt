package com.gb4pc.viewer

import android.app.KeyguardManager
import android.content.BroadcastReceiver
import android.content.ContentUris
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.MediaStore
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ItemTouchHelper
import androidx.recyclerview.widget.ListAdapter
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

    private val sessionTracker get() = SessionTracker.instance

    // L4: BroadcastReceiver to finish the activity when the device unlocks (SF-15)
    private val userPresentReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            if (intent?.action == Intent.ACTION_USER_PRESENT) {
                finish()
            }
        }
    }

    // C3: Pending URI awaiting delete permission grant
    private var pendingDeleteUri: String? = null

    // C3: ActivityResultLauncher for MediaStore delete request (API 30+)
    private val deleteRequestLauncher: ActivityResultLauncher<IntentSenderRequest> =
        registerForActivityResult(ActivityResultContracts.StartIntentSenderForResult()) { result ->
            val uri = pendingDeleteUri
            pendingDeleteUri = null
            if (result.resultCode == RESULT_OK && uri != null) {
                // Permission granted — retry the delete
                retryDeleteFromMediaStore(uri)
            }
            // If cancelled, the item was already removed from session; nothing more to do.
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setShowWhenLocked(true)
        setTurnScreenOn(true)

        setupLayout()
        refreshMedia()
    }

    override fun onStart() {
        super.onStart()
        // L4: Register receiver for device-unlock events
        registerReceiver(userPresentReceiver, IntentFilter(Intent.ACTION_USER_PRESENT))
    }

    override fun onStop() {
        super.onStop()
        // L4: Unregister the unlock receiver
        try {
            unregisterReceiver(userPresentReceiver)
        } catch (e: IllegalArgumentException) {
            DebugLog.log("userPresentReceiver was not registered: ${e.message}")
        }
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

    // H4: ContentObserver registration/unregistration has been removed.
    // The OverlayService registers the ContentObserver and populates SessionTracker.
    // This activity reads SessionTracker directly.

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

    // L5: Null/type-safe access to the ViewPager2's internal RecyclerView
    private fun setupSwipeToDelete() {
        val recyclerView = viewPager.getChildAt(0) as? RecyclerView
        if (recyclerView == null) {
            DebugLog.log("setupSwipeToDelete: ViewPager2 child is not a RecyclerView — swipe-to-delete unavailable")
            return
        }
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

    // C3: Delete with proper scoped-storage permission handling
    private fun deleteFromMediaStore(uriString: String) {
        val uri = Uri.parse(uriString)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            // API 30+: use createDeleteRequest — shows system dialog for items we don't own
            try {
                val pendingIntent = MediaStore.createDeleteRequest(contentResolver, listOf(uri))
                pendingDeleteUri = uriString
                deleteRequestLauncher.launch(
                    IntentSenderRequest.Builder(pendingIntent.intentSender).build()
                )
            } catch (e: Exception) {
                DebugLog.log("Failed to create delete request: ${e.message}")
                runOnUiThread {
                    Toast.makeText(this, R.string.viewer_delete_failed, Toast.LENGTH_SHORT).show()
                }
            }
        } else {
            // API 26–29: attempt direct delete; catch RecoverableSecurityException for items we
            // don't own (API 29) and launch the associated user-action intent
            try {
                val deleted = contentResolver.delete(uri, null, null)
                if (deleted > 0) {
                    DebugLog.log("Deleted media: $uriString")
                } else {
                    DebugLog.log("Delete returned 0 rows for: $uriString")
                    runOnUiThread {
                        Toast.makeText(this, R.string.viewer_delete_failed, Toast.LENGTH_SHORT).show()
                    }
                }
            } catch (e: android.os.RecoverableSecurityException) {
                // API 29: request permission via the embedded action intent
                try {
                    pendingDeleteUri = uriString
                    deleteRequestLauncher.launch(
                        IntentSenderRequest.Builder(e.userAction.actionIntent.intentSender).build()
                    )
                } catch (inner: Exception) {
                    DebugLog.log("Could not launch delete permission UI: ${inner.message}")
                    runOnUiThread {
                        Toast.makeText(this, R.string.viewer_delete_failed, Toast.LENGTH_SHORT).show()
                    }
                }
            } catch (e: Exception) {
                DebugLog.log("Failed to delete media: ${e.message}")
                runOnUiThread {
                    Toast.makeText(this, R.string.viewer_delete_failed, Toast.LENGTH_SHORT).show()
                }
            }
        }
    }

    // C3: Retry after permission was granted by the system dialog
    private fun retryDeleteFromMediaStore(uriString: String) {
        val uri = Uri.parse(uriString)
        try {
            val deleted = contentResolver.delete(uri, null, null)
            if (deleted > 0) {
                DebugLog.log("Deleted media (retry): $uriString")
            } else {
                DebugLog.log("Retry delete returned 0 rows for: $uriString")
                Toast.makeText(this, R.string.viewer_delete_failed, Toast.LENGTH_SHORT).show()
            }
        } catch (e: Exception) {
            DebugLog.log("Retry delete failed: ${e.message}")
            Toast.makeText(this, R.string.viewer_delete_failed, Toast.LENGTH_SHORT).show()
        }
    }

    /**
     * SF-11: Share requires authentication first.
     * L3: UI operations are dispatched to the main thread via runOnUiThread.
     */
    private fun handleShare() {
        val currentPosition = viewPager.currentItem
        val media = adapter.getItemAt(currentPosition) ?: return

        val km = getSystemService(KEYGUARD_SERVICE) as KeyguardManager
        km.requestDismissKeyguard(this, object : KeyguardManager.KeyguardDismissCallback() {
            override fun onDismissSucceeded() {
                runOnUiThread {
                    val shareIntent = Intent(Intent.ACTION_SEND).apply {
                        type = if (media.isVideo) "video/*" else "image/*"
                        putExtra(Intent.EXTRA_STREAM, Uri.parse(media.uri))
                        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    }
                    startActivity(Intent.createChooser(shareIntent, null))
                }
            }

            override fun onDismissError() {}
            override fun onDismissCancelled() {}
        })
    }

    private fun refreshMedia() {
        val media = sessionTracker.getSessionMedia()
        adapter.submitList(media)
        emptyMessage.visibility = if (media.isEmpty()) View.VISIBLE else View.GONE
        viewPager.visibility = if (media.isEmpty()) View.GONE else View.VISIBLE
        shareButton.visibility = if (media.isEmpty()) View.GONE else View.VISIBLE
    }

    /**
     * L6: ViewPager2 adapter using ListAdapter + DiffUtil for efficient updates.
     */
    inner class MediaPagerAdapter : ListAdapter<MediaItem, MediaPagerAdapter.MediaViewHolder>(DIFF_CALLBACK) {

        fun getItemAt(position: Int): MediaItem? = if (position in 0 until itemCount) getItem(position) else null

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
            val item = getItem(position)
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
            return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                contentResolver.loadThumbnail(uri, android.util.Size(1024, 1024), null)
            } else {
                @Suppress("DEPRECATION")
                MediaStore.Images.Media.getBitmap(contentResolver, uri)
            }
        }

        inner class MediaViewHolder(view: View) : RecyclerView.ViewHolder(view)
    }

    companion object {
        // L6: DiffUtil callback — items are the same if they share the same URI
        val DIFF_CALLBACK = object : DiffUtil.ItemCallback<MediaItem>() {
            override fun areItemsTheSame(oldItem: MediaItem, newItem: MediaItem): Boolean =
                oldItem.uri == newItem.uri

            override fun areContentsTheSame(oldItem: MediaItem, newItem: MediaItem): Boolean =
                oldItem == newItem
        }
    }
}
