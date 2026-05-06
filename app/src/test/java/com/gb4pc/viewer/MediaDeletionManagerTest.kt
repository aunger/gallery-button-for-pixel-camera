package com.gb4pc.viewer

import android.app.RemoteAction
import android.content.ContentResolver
import android.net.Uri
import android.os.Build
import androidx.activity.result.IntentSenderRequest
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.mockito.kotlin.any
import org.mockito.kotlin.anyOrNull
import org.mockito.kotlin.doReturn
import org.mockito.kotlin.eq
import org.mockito.kotlin.mock
import org.mockito.kotlin.never
import org.mockito.kotlin.times
import org.mockito.kotlin.verify
import org.mockito.kotlin.whenever

/**
 * Unit tests for [MediaDeletionManager].
 *
 * Covers the API 26–29 code paths and the result-callback retry. The API 30+ path
 * uses the static [android.provider.MediaStore.createDeleteRequest] which can't be
 * stubbed in plain JVM unit tests; it is exercised on emulator via the existing E2E
 * coverage.
 */
class MediaDeletionManagerTest {

    private lateinit var contentResolver: ContentResolver
    private lateinit var uri: Uri
    private var launcherCalls: Int = 0
    private var failureCount: Int = 0

    @Before
    fun setUp() {
        contentResolver = mock()
        uri = mock()
        launcherCalls = 0
        failureCount = 0
    }

    private fun newManager(apiLevel: Int = Build.VERSION_CODES.O): MediaDeletionManager =
        MediaDeletionManager(
            contentResolver = contentResolver,
            launchDeleteRequest = { _: IntentSenderRequest -> launcherCalls++ },
            onFailure = { failureCount++ },
            apiLevel = apiLevel,
        )

    @Test
    fun `API 26 successful delete removes one row and does not invoke onFailure`() {
        whenever(contentResolver.delete(eq(uri), anyOrNull(), anyOrNull())).thenReturn(1)

        newManager(apiLevel = Build.VERSION_CODES.O).delete(uri)

        verify(contentResolver).delete(eq(uri), anyOrNull(), anyOrNull())
        assertEquals(0, failureCount)
        assertEquals("System dialog should not be launched on success", 0, launcherCalls)
    }

    @Test
    fun `API 26 zero-row delete invokes onFailure`() {
        whenever(contentResolver.delete(eq(uri), anyOrNull(), anyOrNull())).thenReturn(0)

        newManager(apiLevel = Build.VERSION_CODES.O).delete(uri)

        assertEquals(1, failureCount)
    }

    @Test
    fun `API 26 generic exception during delete invokes onFailure`() {
        whenever(contentResolver.delete(eq(uri), anyOrNull(), anyOrNull()))
            .thenThrow(RuntimeException("boom"))

        newManager(apiLevel = Build.VERSION_CODES.O).delete(uri)

        assertEquals(1, failureCount)
        assertEquals(0, launcherCalls)
    }

    @Test
    fun `API 29 RecoverableSecurityException launches the embedded IntentSender`() {
        val mockRemoteAction: RemoteAction = mock()
        val mockPendingIntent: android.app.PendingIntent = mock {
            on { intentSender } doReturn mock()
        }
        whenever(mockRemoteAction.actionIntent).thenReturn(mockPendingIntent)
        val securityException = mock<android.app.RecoverableSecurityException> {
            on { userAction } doReturn mockRemoteAction
        }
        whenever(contentResolver.delete(eq(uri), anyOrNull(), anyOrNull())).thenThrow(securityException)

        newManager(apiLevel = Build.VERSION_CODES.Q).delete(uri)

        assertEquals("Should launch system permission UI", 1, launcherCalls)
        assertEquals(0, failureCount)
    }

    @Test
    fun `onDeleteRequestResult with resultOk=true retries the delete`() {
        val mockRemoteAction: RemoteAction = mock()
        val mockPendingIntent: android.app.PendingIntent = mock {
            on { intentSender } doReturn mock()
        }
        whenever(mockRemoteAction.actionIntent).thenReturn(mockPendingIntent)
        val securityException = mock<android.app.RecoverableSecurityException> {
            on { userAction } doReturn mockRemoteAction
        }
        whenever(contentResolver.delete(eq(uri), anyOrNull(), anyOrNull()))
            .thenThrow(securityException)
            .thenReturn(1) // retry succeeds

        val manager = newManager(apiLevel = Build.VERSION_CODES.Q)
        manager.delete(uri)
        assertEquals(1, launcherCalls)

        manager.onDeleteRequestResult(resultOk = true)

        verify(contentResolver, times(2)).delete(eq(uri), anyOrNull(), anyOrNull())
        assertEquals("Retry succeeded — onFailure should not fire", 0, failureCount)
    }

    @Test
    fun `onDeleteRequestResult with resultOk=false does not retry`() {
        val mockRemoteAction: RemoteAction = mock()
        val mockPendingIntent: android.app.PendingIntent = mock {
            on { intentSender } doReturn mock()
        }
        whenever(mockRemoteAction.actionIntent).thenReturn(mockPendingIntent)
        val securityException = mock<android.app.RecoverableSecurityException> {
            on { userAction } doReturn mockRemoteAction
        }
        whenever(contentResolver.delete(eq(uri), anyOrNull(), anyOrNull())).thenThrow(securityException)

        val manager = newManager(apiLevel = Build.VERSION_CODES.Q)
        manager.delete(uri)

        manager.onDeleteRequestResult(resultOk = false)

        // Only the original delete attempt; no retry.
        verify(contentResolver, times(1)).delete(eq(uri), anyOrNull(), anyOrNull())
    }

    @Test
    fun `onDeleteRequestResult with no pendingUri is a no-op`() {
        val manager = newManager(apiLevel = Build.VERSION_CODES.Q)

        manager.onDeleteRequestResult(resultOk = true)

        verify(contentResolver, never()).delete(any(), anyOrNull(), anyOrNull())
        assertEquals(0, failureCount)
    }
}
