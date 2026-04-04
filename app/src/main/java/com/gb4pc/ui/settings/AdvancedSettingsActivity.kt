package com.gb4pc.ui.settings

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import com.gb4pc.Constants
import com.gb4pc.R
import com.gb4pc.data.AspectRatioUtil
import com.gb4pc.data.OverlayPosition
import com.gb4pc.data.PrefsManager
import com.gb4pc.util.DebugLog
import java.text.SimpleDateFormat
import java.util.*

/**
 * Advanced Settings screen (§6.3, UI-10).
 */
class AdvancedSettingsActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                AdvancedSettingsScreen(PrefsManager(this))
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AdvancedSettingsScreen(prefsManager: PrefsManager) {
    val context = LocalContext.current
    val display = context.resources.displayMetrics
    val aspectRatio = AspectRatioUtil.quantize(display.widthPixels, display.heightPixels)
    val currentPosition = prefsManager.getOverlayPosition(aspectRatio)

    var xPercent by remember { mutableFloatStateOf(currentPosition.xPercent) }
    var yPercent by remember { mutableFloatStateOf(currentPosition.yPercent) }
    var sizePercent by remember { mutableFloatStateOf(currentPosition.sizePercent) }
    var showResetDialog by remember { mutableStateOf(false) }

    // Save on change (UI-11)
    fun savePosition() {
        val pos = OverlayPosition(xPercent, yPercent, sizePercent)
        prefsManager.saveOverlayPosition(aspectRatio, pos)
    }

    Scaffold(
        topBar = {
            TopAppBar(title = { Text(stringResource(R.string.advanced_title)) })
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .padding(16.dp)
                .fillMaxWidth()
        ) {
            // UI-10.1: Position sliders
            Text(
                text = stringResource(R.string.advanced_x_position),
                style = MaterialTheme.typography.labelLarge
            )
            Text(text = "%.2f%%".format(xPercent), style = MaterialTheme.typography.bodySmall)
            Slider(
                value = xPercent,
                onValueChange = { xPercent = it; savePosition() },
                valueRange = 0f..100f,
                modifier = Modifier.padding(bottom = 16.dp)
            )

            Text(
                text = stringResource(R.string.advanced_y_position),
                style = MaterialTheme.typography.labelLarge
            )
            Text(text = "%.2f%%".format(yPercent), style = MaterialTheme.typography.bodySmall)
            Slider(
                value = yPercent,
                onValueChange = { yPercent = it; savePosition() },
                valueRange = 0f..100f,
                modifier = Modifier.padding(bottom = 16.dp)
            )

            Text(
                text = stringResource(R.string.advanced_size),
                style = MaterialTheme.typography.labelLarge
            )
            Text(text = "%.2f%%".format(sizePercent), style = MaterialTheme.typography.bodySmall)
            Slider(
                value = sizePercent,
                onValueChange = { sizePercent = it; savePosition() },
                valueRange = Constants.MIN_SIZE_PERCENT..Constants.MAX_SIZE_PERCENT,
                modifier = Modifier.padding(bottom = 16.dp)
            )

            // UI-10.2: Reset to defaults
            OutlinedButton(
                onClick = { showResetDialog = true },
                modifier = Modifier.fillMaxWidth().padding(bottom = 24.dp)
            ) {
                Text(stringResource(R.string.advanced_reset))
            }

            // UI-10.3: Debug log
            Text(
                text = stringResource(R.string.advanced_debug_log),
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.padding(bottom = 8.dp)
            )

            val debugEntries = remember { DebugLog.getEntries() }
            val dateFormat = remember { SimpleDateFormat("HH:mm:ss.SSS", Locale.US) }

            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f)
            ) {
                if (debugEntries.isEmpty()) {
                    Text(
                        text = "No log entries",
                        modifier = Modifier.padding(16.dp),
                        style = MaterialTheme.typography.bodySmall
                    )
                } else {
                    LazyColumn(modifier = Modifier.padding(8.dp)) {
                        items(debugEntries.reversed()) { entry ->
                            Text(
                                text = "${dateFormat.format(Date(entry.timestamp))}  ${entry.message}",
                                style = MaterialTheme.typography.bodySmall,
                                modifier = Modifier.padding(vertical = 2.dp)
                            )
                        }
                    }
                }
            }
        }

        // Reset confirmation dialog
        if (showResetDialog) {
            AlertDialog(
                onDismissRequest = { showResetDialog = false },
                title = { Text(stringResource(R.string.advanced_reset_confirm_title)) },
                text = { Text(stringResource(R.string.advanced_reset_confirm_message)) },
                confirmButton = {
                    TextButton(onClick = {
                        prefsManager.resetOverlayPosition(aspectRatio)
                        val defaultPos = OverlayPosition.default()
                        xPercent = defaultPos.xPercent
                        yPercent = defaultPos.yPercent
                        sizePercent = defaultPos.sizePercent
                        showResetDialog = false
                    }) {
                        Text("Reset")
                    }
                },
                dismissButton = {
                    TextButton(onClick = { showResetDialog = false }) {
                        Text("Cancel")
                    }
                }
            )
        }
    }
}
