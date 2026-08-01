# GB4PC ProGuard Rules

# Keep overlay service
-keep class com.gb4pc.service.OverlayService { *; }

# Keep boot receiver
-keep class com.gb4pc.receiver.BootReceiver { *; }

# Keep viewer activity (launched from overlay)
-keep class com.gb4pc.viewer.SecureViewerActivity { *; }

# Subsampling Scale Image View
-keep class com.davemorrissey.labs.subscaleview.** { *; }

# AGP 9.1 flips R8 to repackage all classes into the default package by default. The release APK
# has no runtime test coverage, so its bytecode layout must not change in the same commit as the
# toolchain; opt out here and revisit repackaging as its own change.
-dontrepackage
