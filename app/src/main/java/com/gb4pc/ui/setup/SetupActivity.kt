package com.gb4pc.ui.setup

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.gb4pc.R
import com.gb4pc.data.PrefsManager
import com.gb4pc.util.PermissionHelper

/**
 * Guided setup flow activity (§2.2, PM-01 through PM-05).
 */
class SetupActivity : ComponentActivity() {

    private lateinit var prefsManager: PrefsManager
    private val setupState = SetupState()

    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* Handled in onResume */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefsManager = PrefsManager(this)
    }

    override fun onResume() {
        super.onResume()
        // PM-02: Auto-advance when permission is detected as granted
        autoAdvanceIfGranted()
        updateUI()
    }

    private fun autoAdvanceIfGranted() {
        while (!setupState.isCompleted && isCurrentStepGranted()) {
            setupState.advance()
        }
        if (setupState.isCompleted) {
            prefsManager.isSetupCompleted = true
            finish()
        }
    }

    private fun isCurrentStepGranted(): Boolean = when (setupState.currentStep) {
        SetupStep.NOTIFICATION -> PermissionHelper.hasNotificationPermission(this)
        SetupStep.USAGE_ACCESS -> PermissionHelper.hasUsageStatsPermission(this)
        SetupStep.OVERLAY -> PermissionHelper.hasOverlayPermission(this)
        SetupStep.BATTERY -> PermissionHelper.isBatteryOptimizationExcluded(this)
    }

    private fun updateUI() {
        if (setupState.isCompleted) return

        setContent {
            MaterialTheme {
                SetupScreen(
                    currentStep = setupState.currentStep,
                    onGrantClick = { handleGrant(setupState.currentStep) },
                    onSkipClick = {
                        setupState.advance()
                        if (setupState.isCompleted) {
                            prefsManager.isSetupCompleted = true
                            finish()
                        } else {
                            updateUI()
                        }
                    }
                )
            }
        }
    }

    private fun handleGrant(step: SetupStep) {
        when (step) {
            SetupStep.NOTIFICATION -> {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
                }
            }
            SetupStep.USAGE_ACCESS -> {
                startActivity(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
            }
            SetupStep.OVERLAY -> {
                startActivity(
                    Intent(
                        Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                        Uri.parse("package:$packageName")
                    )
                )
            }
            SetupStep.BATTERY -> {
                startActivity(
                    Intent(
                        Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                        Uri.parse("package:$packageName")
                    )
                )
            }
        }
    }
}

@Composable
fun SetupScreen(
    currentStep: SetupStep,
    onGrantClick: () -> Unit,
    onSkipClick: () -> Unit
) {
    val (title, description, buttonText) = when (currentStep) {
        SetupStep.NOTIFICATION -> Triple(
            stringResource(R.string.setup_notification_title),
            stringResource(R.string.setup_notification_desc),
            stringResource(R.string.setup_notification_button)
        )
        SetupStep.USAGE_ACCESS -> Triple(
            stringResource(R.string.setup_usage_access_title),
            stringResource(R.string.setup_usage_access_desc),
            stringResource(R.string.setup_usage_access_button)
        )
        SetupStep.OVERLAY -> Triple(
            stringResource(R.string.setup_overlay_title),
            stringResource(R.string.setup_overlay_desc),
            stringResource(R.string.setup_overlay_button)
        )
        SetupStep.BATTERY -> Triple(
            stringResource(R.string.setup_battery_title),
            stringResource(R.string.setup_battery_desc),
            stringResource(R.string.setup_battery_button)
        )
    }

    Surface(modifier = Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(32.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Text(
                text = stringResource(R.string.setup_title),
                style = MaterialTheme.typography.headlineSmall,
                modifier = Modifier.padding(bottom = 48.dp)
            )

            Icon(
                Icons.Default.CheckCircle,
                contentDescription = null,
                modifier = Modifier.size(64.dp).padding(bottom = 16.dp),
                tint = MaterialTheme.colorScheme.primary
            )

            Text(
                text = title,
                style = MaterialTheme.typography.titleLarge,
                modifier = Modifier.padding(bottom = 16.dp)
            )

            Text(
                text = description,
                style = MaterialTheme.typography.bodyMedium,
                textAlign = TextAlign.Center,
                modifier = Modifier.padding(bottom = 32.dp)
            )

            Button(
                onClick = onGrantClick,
                modifier = Modifier.fillMaxWidth()
            ) {
                Text(buttonText)
            }

            TextButton(
                onClick = onSkipClick,
                modifier = Modifier.padding(top = 16.dp)
            ) {
                Text(stringResource(R.string.setup_skip))
            }
        }
    }
}
