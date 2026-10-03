package com.example

import android.annotation.SuppressLint
import android.graphics.Bitmap
import android.graphics.Color
import android.os.Bundle
import android.util.Log
import android.view.ViewGroup
import android.webkit.ConsoleMessage
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.core.view.WindowInsetsControllerCompat
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream

class MainActivity : ComponentActivity() {

    private lateinit var webView: WebView
    private val coroutineScope = CoroutineScope(Dispatchers.Main + SupervisorJob())
    private var isPageLoaded = false
    private lateinit var shim: PyObject

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Set status bar and navigation bar appearance
        window.statusBarColor = Color.parseColor("#020713")
        window.navigationBarColor = Color.parseColor("#020713")
        WindowInsetsControllerCompat(window, window.decorView).apply {
            isAppearanceLightStatusBars = false
            isAppearanceLightNavigationBars = false
        }

        // 1. Copy logo assets if present to internal filesDir so get_image_base64() finds them
        copyAssetsToFilesDir()

        // 2. Start Chaquopy Python runtime
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(this))
        }
        val python = Python.getInstance()
        shim = python.getModule("monero_shim")

        // 3. Initialize shim with filesDir (sets sys.frozen and sys.executable)
        shim.callAttr("initialize", filesDir.absolutePath)

        // 4. Create and configure full-screen WebView
        webView = WebView(this).apply {
            layoutParams = ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
            setBackgroundColor(Color.parseColor("#020713"))
            isVerticalScrollBarEnabled = true
            isHorizontalScrollBarEnabled = false
            isFocusable = true
            isFocusableInTouchMode = true
        }
        setContentView(webView)

        configureWebSettings(webView.settings)

        // 5. Add JavaScript interface bridging pywebview API to Chaquopy
        webView.addJavascriptInterface(AndroidBridge(webView, shim, coroutineScope), "_AndroidBridge")

        // 6. Setup WebView clients
        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                isPageLoaded = true
                view?.let { injectPolyfillAndReady(it) }
            }

            override fun onPageStarted(view: WebView?, url: String?, favicon: Bitmap?) {
                super.onPageStarted(view, url, favicon)
                view?.let { injectPolyfillAndReady(it) }
            }
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onConsoleMessage(message: ConsoleMessage?): Boolean {
                Log.d("MoneroLiquidWeb", "${message?.message()} -- line ${message?.lineNumber()}")
                return true
            }
        }

        // 7. Handle back navigation
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.canGoBack()) {
                    webView.goBack()
                } else {
                    finish()
                }
            }
        })

        // 8. Generate HTML via Python and load with https base URL so CDNs load properly
        val htmlContent = shim.callAttr("get_html_content").toString()
        webView.loadDataWithBaseURL(
            "https://hashvault.pro/",
            htmlContent,
            "text/html",
            "UTF-8",
            null
        )
    }

    private fun configureWebSettings(settings: WebSettings) {
        settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            useWideViewPort = true
            loadWithOverviewMode = true
            displayZoomControls = false
            builtInZoomControls = false
            setSupportZoom(false)
            allowFileAccess = false
            allowContentAccess = false
            mediaPlaybackRequiresUserGesture = false
            cacheMode = WebSettings.LOAD_DEFAULT
        }
    }

    private fun injectPolyfillAndReady(view: WebView) {
        val polyfillJs = """
            (function() {
                if (window._bridge_injected) return;
                window._bridge_injected = true;
                window._pywebview_callbacks = window._pywebview_callbacks || {};
                window.pywebview = {
                    api: {
                        get_initial_wallet: function() {
                            return new Promise(function(resolve) {
                                var w = window._AndroidBridge.getInitialWallet();
                                resolve(w);
                            });
                        },
                        get_stats: function(wallet) {
                            return new Promise(function(resolve, reject) {
                                var cbId = 'cb_' + Date.now() + '_' + Math.random().toString(36).substring(2, 9);
                                window._pywebview_callbacks[cbId] = {
                                    resolve: resolve,
                                    reject: reject
                                };
                                window._AndroidBridge.requestStats(wallet || '', cbId);
                            });
                        }
                    }
                };
                window.dispatchEvent(new Event('pywebviewready'));
            })();
        """.trimIndent()
        view.evaluateJavascript(polyfillJs, null)
    }

    private fun copyAssetsToFilesDir() {
        val assetsList = listOf("monero.png", "coin.png")
        for (filename in assetsList) {
            val destFile = File(filesDir, filename)
            if (!destFile.exists()) {
                try {
                    assets.open(filename).use { input ->
                        FileOutputStream(destFile).use { output ->
                            input.copyTo(output)
                        }
                    }
                    Log.i("MoneroLiquid", "Copied asset $filename to internal files dir")
                } catch (e: Exception) {
                    Log.w("MoneroLiquid", "Asset $filename not found in APK, using fallback logo")
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        webView.onResume()
        webView.resumeTimers()
        // Immediately refresh when returning to foreground
        if (isPageLoaded) {
            webView.evaluateJavascript("if (typeof syncData === 'function') { syncData(); }", null)
        }
    }

    override fun onPause() {
        super.onPause()
        webView.onPause()
    }

    override fun onDestroy() {
        super.onDestroy()
        coroutineScope.cancel()
        webView.destroy()
    }

    /**
     * JavaScriptInterface bridging WebView calls to Chaquopy Python.
     * All network and Python operations run strictly on Dispatchers.IO.
     */
    private class AndroidBridge(
        private val webView: WebView,
        private val shim: PyObject,
        private val coroutineScope: CoroutineScope
    ) {
        private var isFirstFetch = true

        @JavascriptInterface
        fun getInitialWallet(): String {
            return try {
                shim.callAttr("get_initial_wallet").toString()
            } catch (e: Exception) {
                Log.e("AndroidBridge", "Error in getInitialWallet", e)
                ""
            }
        }

        @JavascriptInterface
        fun requestStats(wallet: String, callbackId: String) {
            val firstLaunch = isFirstFetch
            isFirstFetch = false

            coroutineScope.launch(Dispatchers.IO) {
                // If on initial launch, immediately send cached data so dashboard is not empty
                if (firstLaunch) {
                    val cachedStatsJson = try {
                        shim.callAttr("get_cached_stats_json").toString()
                    } catch (e: Exception) {
                        ""
                    }
                    if (cachedStatsJson.isNotBlank()) {
                        withContext(Dispatchers.Main) {
                            val jsUpdate = "if (typeof updateUI === 'function') { updateUI($cachedStatsJson); }"
                            webView.evaluateJavascript(jsUpdate, null)
                        }
                    }
                }

                // Live request in background thread
                val liveStatsJson = try {
                    shim.callAttr("fetch_stats", wallet).toString()
                } catch (e: Exception) {
                    Log.e("AndroidBridge", "Exception in fetch_stats", e)
                    JSONObject().put("error", e.message ?: "Unknown error").toString()
                }

                // Resolve promise on Main thread
                withContext(Dispatchers.Main) {
                    val jsResolve = """
                        (function() {
                            if (window._pywebview_callbacks && window._pywebview_callbacks['$callbackId']) {
                                window._pywebview_callbacks['$callbackId'].resolve($liveStatsJson);
                                delete window._pywebview_callbacks['$callbackId'];
                            }
                        })();
                    """.trimIndent()
                    webView.evaluateJavascript(jsResolve, null)
                }
            }
        }
    }
}
