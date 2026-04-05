package com.gb4pc.ui.picker

import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.drawable.Drawable
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.core.graphics.drawable.toBitmap
import com.gb4pc.R
import com.gb4pc.data.PrefsManager
import com.gb4pc.util.DebugLog

/**
 * Gallery app picker (§6.2).
 * Can be launched from settings (UI-07) or JIT from the overlay (UI-08).
 */
class PickerActivity : ComponentActivity() {

    companion object {
        const val EXTRA_LAUNCH_AFTER_PICK = "launch_after_pick"
    }

    private lateinit var prefsManager: PrefsManager
    private var launchAfterPick = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefsManager = PrefsManager(this)
        launchAfterPick = intent.getBooleanExtra(EXTRA_LAUNCH_AFTER_PICK, false)

        setContent {
            MaterialTheme {
                PickerScreen(
                    prefsManager = prefsManager,
                    onAppSelected = { packageName ->
                        handleSelection(packageName)
                    }
                )
            }
        }
    }

    private fun handleSelection(packageName: String) {
        prefsManager.galleryPackage = packageName
        DebugLog.log("Gallery app selected: $packageName")

        // UI-08: If launched JIT from overlay, launch the gallery app immediately
        if (launchAfterPick) {
            val launchIntent = packageManager.getLaunchIntentForPackage(packageName)
            if (launchIntent != null) {
                launchIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                startActivity(launchIntent)
            }
        }
        finish()
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PickerScreen(
    prefsManager: PrefsManager,
    onAppSelected: (String) -> Unit
) {
    val context = LocalContext.current
    var searchQuery by remember { mutableStateOf("") }

    // Load all launchable apps off the main thread
    var allApps by remember { mutableStateOf(emptyList<AppInfo>()) }
    LaunchedEffect(Unit) {
        allApps = withContext(Dispatchers.IO) {
            val pm = context.packageManager
            val mainIntent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
            val resolveInfos = pm.queryIntentActivities(mainIntent, PackageManager.MATCH_ALL)
            resolveInfos.map { ri ->
                AppInfo(
                    label = ri.loadLabel(pm).toString(),
                    packageName = ri.activityInfo.packageName
                )
            }.distinctBy { it.packageName }
        }
    }

    val filteredApps = remember(allApps, searchQuery) {
        AppListFilter.filter(allApps, searchQuery, context.packageName)
    }

    Scaffold(
        topBar = {
            TopAppBar(title = { Text(stringResource(R.string.picker_title)) })
        }
    ) { padding ->
        Column(modifier = Modifier.padding(padding)) {
            // UI-06: Search bar
            OutlinedTextField(
                value = searchQuery,
                onValueChange = { searchQuery = it },
                placeholder = { Text(stringResource(R.string.picker_search_hint)) },
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                singleLine = true
            )

            // UI-05: App list
            LazyColumn {
                items(filteredApps, key = { it.packageName }) { app ->
                    AppRow(app = app, onClick = { onAppSelected(app.packageName) })
                }
            }
        }
    }
}

@Composable
fun AppRow(app: AppInfo, onClick: () -> Unit) {
    val context = LocalContext.current
    val icon: Drawable? = remember(app.packageName) {
        try {
            context.packageManager.getApplicationIcon(app.packageName)
        } catch (_: Exception) {
            null
        }
    }

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        icon?.let {
            Image(
                bitmap = it.toBitmap(48, 48).asImageBitmap(),
                contentDescription = app.label,
                modifier = Modifier.size(48.dp)
            )
        }
        Spacer(modifier = Modifier.width(16.dp))
        Column {
            Text(text = app.label, style = MaterialTheme.typography.bodyLarge)
            Text(
                text = app.packageName,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
    }
}
