package com.gb4pc.viewer

/**
 * A media item captured during the current secure camera session (SF-04).
 */
data class MediaItem(
    val uri: String,
    val dateTaken: Long,
    val isVideo: Boolean
)
