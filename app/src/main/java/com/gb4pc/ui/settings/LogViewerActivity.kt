package com.gb4pc.ui.settings

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import com.gb4pc.R
import com.gb4pc.ui.theme.GB4PCTheme
import com.gb4pc.util.DebugLog
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Dedicated screen for viewing the in-memory debug log (issue #83).
 *
 * Entries are displayed oldest-first (newest at the bottom) with auto-scroll
 * to follow the latest entry. A "Clear log" button wipes the buffer.
 */
class LogViewerActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            GB4PCTheme {
                LogViewerScreen(onNavigateUp = { finish() })
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LogViewerScreen(onNavigateUp: () -> Unit = {}) {
    var entries by remember { mutableStateOf(DebugLog.getEntries()) }

    DisposableEffect(Unit) {
        DebugLog.listener = { entries = DebugLog.getEntries() }
        onDispose { DebugLog.listener = null }
    }

    val listState = rememberLazyListState()
    val dateFormat = remember { SimpleDateFormat("HH:mm:ss.SSS", Locale.US) }

    // Auto-scroll to the bottom whenever the entry list changes
    LaunchedEffect(entries) {
        if (entries.isNotEmpty()) {
            listState.animateScrollToItem(entries.size - 1)
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.log_viewer_title)) },
                navigationIcon = {
                    IconButton(onClick = onNavigateUp) {
                        Icon(
                            Icons.AutoMirrored.Filled.ArrowBack,
                            contentDescription = stringResource(R.string.log_viewer_navigate_up),
                        )
                    }
                },
                actions = {
                    TextButton(onClick = { DebugLog.clear() }) {
                        Text(stringResource(R.string.log_viewer_clear))
                    }
                },
            )
        },
    ) { padding ->
        if (entries.isEmpty()) {
            Box(
                modifier =
                    Modifier
                        .padding(padding)
                        .fillMaxSize(),
            ) {
                Text(
                    text = stringResource(R.string.log_viewer_empty),
                    modifier = Modifier.padding(16.dp),
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        } else {
            SelectionContainer(
                modifier =
                    Modifier
                        .padding(padding)
                        .fillMaxSize(),
            ) {
                LazyColumn(
                    state = listState,
                    modifier =
                        Modifier
                            .fillMaxSize()
                            .padding(horizontal = 8.dp),
                    contentPadding = PaddingValues(vertical = 8.dp),
                ) {
                    items(entries) { entry ->
                        Text(
                            text = "${dateFormat.format(Date(entry.timestamp))}  ${entry.message}",
                            style = MaterialTheme.typography.bodySmall,
                            modifier = Modifier.padding(vertical = 2.dp),
                        )
                    }
                }
            }
        }
    }
}
