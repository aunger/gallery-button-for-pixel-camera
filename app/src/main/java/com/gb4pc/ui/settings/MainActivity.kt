package com.gb4pc.ui.settings

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import com.gb4pc.R
import com.gb4pc.data.PrefsManager
import com.gb4pc.service.OverlayService
import com.gb4pc.ui.picker.PickerActivity
import com.gb4pc.ui.setup.SetupActivity
import com.gb4pc.util.PermissionHelper

class MainActivity : ComponentActivity() {

    private lateinit var prefsManager: PrefsManager

    // Permission states updated in onResume so Compose reacts to changes
    private var isPixelCameraInstalled = mutableStateOf(false)
    private var hasUsageStats = mutableStateOf(false)
    private var hasOverlay = mutableStateOf(false)
    private var isBatteryExcluded = mutableStateOf(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefsManager = PrefsManager(this)

        // Redirect to setup if not completed
        if (!prefsManager.isSetupCompleted) {
            startActivity(Intent(this, SetupActivity::class.java))
            finish()
            return
        }

        // setContent called only once here (H2 fix)
        setContent {
            MaterialTheme {
                MainSettingsScreen(
                    prefsManager = prefsManager,
                    isPixelCameraInstalled = isPixelCameraInstalled.value,
                    hasUsageStats = hasUsageStats.value,
                    hasOverlay = hasOverlay.value,
                    isBatteryExcluded = isBatteryExcluded.value
                )
            }
        }
    }

    override fun onResume() {
        super.onResume()
        // Re-check permissions on every resume so banners appear/disappear correctly (H1 fix)
        isPixelCameraInstalled.value = PermissionHelper.isPixelCameraInstalled(this)
        hasUsageStats.value = PermissionHelper.hasUsageStatsPermission(this)
        hasOverlay.value = PermissionHelper.hasOverlayPermission(this)
        isBatteryExcluded.value = PermissionHelper.isBatteryOptimizationExcluded(this)
    }
}

@Composable
fun MainSettingsScreen(
    prefsManager: PrefsManager,
    isPixelCameraInstalled: Boolean,
    hasUsageStats: Boolean,
    hasOverlay: Boolean,
    isBatteryExcluded: Boolean
) {
    val context = LocalContext.current
    var isServiceEnabled by remember { mutableStateOf(prefsManager.isServiceEnabled) }
    var galleryPackage by remember { mutableStateOf(prefsManager.galleryPackage) }

    Surface(modifier = Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier
                .padding(16.dp)
                .fillMaxWidth()
        ) {
            Text(
                text = stringResource(R.string.app_name),
                style = MaterialTheme.typography.headlineMedium,
                modifier = Modifier.padding(bottom = 16.dp)
            )

            // UI-03: Pixel Camera not installed notice
            if (!isPixelCameraInstalled) {
                Card(
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.errorContainer
                    ),
                    modifier = Modifier.fillMaxWidth().padding(bottom = 16.dp)
                ) {
                    Text(
                        text = stringResource(R.string.settings_pixel_camera_missing),
                        modifier = Modifier.padding(16.dp),
                        color = MaterialTheme.colorScheme.onErrorContainer
                    )
                }
            }

            // UI-02: Missing permission banners
            if (!hasUsageStats) {
                PermissionBanner(
                    message = stringResource(R.string.settings_permission_missing, "Usage Access"),
                    onClick = {
                        context.startActivity(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
                    }
                )
            }
            if (!hasOverlay) {
                PermissionBanner(
                    message = stringResource(R.string.settings_permission_missing, "Draw Over Apps"),
                    onClick = {
                        context.startActivity(
                            Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                                Uri.parse("package:${context.packageName}"))
                        )
                    }
                )
            }

            // UI-04: Battery optimization warning
            if (!isBatteryExcluded) {
                PermissionBanner(
                    message = stringResource(R.string.settings_battery_warning),
                    onClick = {
                        context.startActivity(
                            Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                                Uri.parse("package:${context.packageName}"))
                        )
                    }
                )
            }

            // UI-01.1: Master toggle
            if (isPixelCameraInstalled) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 12.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            text = stringResource(R.string.settings_service_toggle),
                            style = MaterialTheme.typography.titleMedium
                        )
                    }
                    Switch(
                        checked = isServiceEnabled,
                        onCheckedChange = { enabled ->
                            isServiceEnabled = enabled
                            prefsManager.isServiceEnabled = enabled
                            if (enabled) {
                                OverlayService.start(context)
                            } else {
                                OverlayService.stop(context)
                            }
                        }
                    )
                }

                HorizontalDivider()

                // UI-01.2: Gallery app row
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable {
                            context.startActivity(Intent(context, PickerActivity::class.java))
                        }
                        .padding(vertical = 16.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            text = stringResource(R.string.settings_gallery_app),
                            style = MaterialTheme.typography.titleMedium
                        )
                        Text(
                            text = galleryPackage
                                ?.let { getAppLabel(context, it) }
                                ?: stringResource(R.string.settings_gallery_not_set),
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }

                HorizontalDivider()

                // UI-01.3: Advanced Settings
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable {
                            context.startActivity(Intent(context, AdvancedSettingsActivity::class.java))
                        }
                        .padding(vertical = 16.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Icon(Icons.Default.Settings, contentDescription = null, modifier = Modifier.padding(end = 12.dp))
                    Text(
                        text = stringResource(R.string.settings_advanced),
                        style = MaterialTheme.typography.titleMedium
                    )
                }
            }
        }
    }
}

@Composable
fun PermissionBanner(message: String, onClick: () -> Unit) {
    Card(
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.tertiaryContainer
        ),
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 8.dp)
            .clickable(onClick = onClick)
    ) {
        Row(
            modifier = Modifier.padding(12.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Icon(
                Icons.Default.Warning,
                contentDescription = null,
                modifier = Modifier.padding(end = 8.dp),
                tint = MaterialTheme.colorScheme.onTertiaryContainer
            )
            Text(
                text = message,
                color = MaterialTheme.colorScheme.onTertiaryContainer,
                style = MaterialTheme.typography.bodySmall
            )
        }
    }
}

private fun getAppLabel(context: android.content.Context, packageName: String): String {
    return try {
        val pm = context.packageManager
        val appInfo = pm.getApplicationInfo(packageName, 0)
        pm.getApplicationLabel(appInfo).toString()
    } catch (_: Exception) {
        packageName
    }
}
