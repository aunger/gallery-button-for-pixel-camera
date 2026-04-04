# GB4PC ProGuard Rules

# Keep overlay service
-keep class com.gb4pc.service.OverlayService { *; }

# Keep boot receiver
-keep class com.gb4pc.receiver.BootReceiver { *; }

# Keep viewer activity (launched from overlay)
-keep class com.gb4pc.viewer.SecureViewerActivity { *; }

# Subsampling Scale Image View
-keep class com.davemorrissey.labs.subscaleview.** { *; }
