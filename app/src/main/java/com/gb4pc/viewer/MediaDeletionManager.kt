package com.gb4pc.viewer

import android.app.RecoverableSecurityException
import android.content.ContentResolver
import android.content.IntentSender
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import androidx.activity.result.IntentSenderRequest
import androidx.annotation.ChecksSdkIntAtLeast
import androidx.annotation.RequiresApi
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
 *   - **API 26-28** never throws `RecoverableSecurityException`; a direct
 *     `ContentResolver.delete` either succeeds or fails outright.
 *
 * Extracted from `SecureViewerActivity` so the version dispatch is testable in
 * isolation and the activity stays focused on UI concerns (snackbar/undo,
 * ViewPager wiring, etc.).
 *
 * [apiLevel] stands in for `Build.VERSION.SDK_INT` so plain JVM tests can choose a
 * branch; production never passes anything but the default. Every branch on it goes
 * through [isAtLeast], whose annotation tells Android Lint's NewApi check to treat it
 * as the SDK check it is in production.
 */
class MediaDeletionManager(
    private val contentResolver: ContentResolver,
    private val launchDeleteRequest: (IntentSenderRequest) -> Unit,
    private val onFailure: () -> Unit,
    private val apiLevel: Int = Build.VERSION.SDK_INT,
) {
    private var pendingUri: Uri? = null

    @ChecksSdkIntAtLeast(parameter = 0)
    private fun isAtLeast(api: Int): Boolean = apiLevel >= api

    /**
     * Attempt to delete [uri]. Returns immediately whether the delete completed
     * synchronously, threw a recoverable exception (in which case a system dialog is
     * launched and the result will arrive via [onDeleteRequestResult]), or failed.
     */
    fun delete(uri: Uri) {
        if (isAtLeast(Build.VERSION_CODES.R)) {
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

    @RequiresApi(Build.VERSION_CODES.R)
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
        } catch (e: Exception) {
            if (e is SecurityException && isAtLeast(Build.VERSION_CODES.Q) && Api29.isRecoverable(e)) {
                // API 29: request permission via the embedded action intent
                try {
                    pendingUri = uri
                    launchDeleteRequest(IntentSenderRequest.Builder(Api29.userActionSender(e)).build())
                } catch (inner: Exception) {
                    DebugLog.log("Could not launch delete permission UI: ${inner.message}")
                    onFailure()
                }
            } else {
                DebugLog.log("Failed to delete media: ${e.message}")
                onFailure()
            }
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

    /**
     * Every reference to `RecoverableSecurityException`, which API 29 introduced. On API 26-28
     * the class does not exist, so it is named only here, in code that runs on API 29+, and not
     * in a `catch` clause of [attemptDeleteApi26To29], which also runs on API 26-28.
     */
    @RequiresApi(Build.VERSION_CODES.Q)
    private object Api29 {
        fun isRecoverable(e: SecurityException): Boolean = e is RecoverableSecurityException

        fun userActionSender(e: SecurityException): IntentSender = (e as RecoverableSecurityException).userAction.actionIntent.intentSender
    }
}
