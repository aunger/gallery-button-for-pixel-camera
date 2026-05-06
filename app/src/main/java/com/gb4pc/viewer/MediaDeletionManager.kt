package com.gb4pc.viewer

import android.app.RecoverableSecurityException
import android.content.ContentResolver
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import androidx.activity.result.IntentSenderRequest
import com.gb4pc.util.DebugLog

/**
 * Owns the API-version-conditional MediaStore delete dance for the secure viewer.
 *
 * Delete on Android scoped storage is awkward:
 *   - **API 30+** must use `MediaStore.createDeleteRequest`, which produces an
 *     `IntentSender` that launches a system confirmation dialog. We hand the
 *     IntentSender to the activity's `ActivityResultLauncher` (passed in as
 *     [launchDeleteRequest]); the activity calls [onDeleteRequestResult] when
 *     the user finishes the dialog, and the manager retries the raw delete.
 *   - **API 29** can throw `RecoverableSecurityException` for items the app
 *     doesn't own; the exception carries an `IntentSender` that we hand off
 *     the same way.
 *   - **API 26–28** never throws `RecoverableSecurityException`; a direct
 *     `ContentResolver.delete` either succeeds or fails outright.
 *
 * Extracted from `SecureViewerActivity` so the version dispatch is testable in
 * isolation and the activity stays focused on UI concerns (snackbar/undo,
 * ViewPager wiring, etc.).
 */
class MediaDeletionManager(
    private val contentResolver: ContentResolver,
    private val launchDeleteRequest: (IntentSenderRequest) -> Unit,
    private val onFailure: () -> Unit,
    private val apiLevel: Int = Build.VERSION.SDK_INT,
) {
    private var pendingUri: Uri? = null

    /**
     * Attempt to delete [uri]. Returns immediately whether the delete completed
     * synchronously, threw a recoverable exception (in which case a system dialog is
     * launched and the result will arrive via [onDeleteRequestResult]), or failed.
     */
    fun delete(uri: Uri) {
        if (apiLevel >= Build.VERSION_CODES.R) {
            requestDeleteApi30Plus(uri)
        } else {
            attemptDeleteApi26To29(uri)
        }
    }

    /** Called by the host activity from its `ActivityResultLauncher` result callback. */
    fun onDeleteRequestResult(resultOk: Boolean) {
        val uri = pendingUri ?: return
        pendingUri = null
        if (resultOk) {
            retryRawDelete(uri)
        }
        // If cancelled, the item was already removed from the in-memory session;
        // nothing more to do.
    }

    private fun requestDeleteApi30Plus(uri: Uri) {
        try {
            val pendingIntent = MediaStore.createDeleteRequest(contentResolver, listOf(uri))
            pendingUri = uri
            launchDeleteRequest(IntentSenderRequest.Builder(pendingIntent.intentSender).build())
        } catch (e: Exception) {
            DebugLog.log("Failed to create delete request: ${e.message}")
            onFailure()
        }
    }

    private fun attemptDeleteApi26To29(uri: Uri) {
        try {
            val deleted = contentResolver.delete(uri, null, null)
            if (deleted > 0) {
                DebugLog.log("Deleted media: $uri")
            } else {
                DebugLog.log("Delete returned 0 rows for: $uri")
                onFailure()
            }
        } catch (e: RecoverableSecurityException) {
            // API 29: request permission via the embedded action intent
            try {
                pendingUri = uri
                launchDeleteRequest(
                    IntentSenderRequest.Builder(e.userAction.actionIntent.intentSender).build()
                )
            } catch (inner: Exception) {
                DebugLog.log("Could not launch delete permission UI: ${inner.message}")
                onFailure()
            }
        } catch (e: Exception) {
            DebugLog.log("Failed to delete media: ${e.message}")
            onFailure()
        }
    }

    private fun retryRawDelete(uri: Uri) {
        try {
            val deleted = contentResolver.delete(uri, null, null)
            if (deleted > 0) {
                DebugLog.log("Deleted media (retry): $uri")
            } else {
                DebugLog.log("Retry delete returned 0 rows for: $uri")
                onFailure()
            }
        } catch (e: Exception) {
            DebugLog.log("Retry delete failed: ${e.message}")
            onFailure()
        }
    }
}
