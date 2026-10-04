// The dictation window: the page the service serves (http://127.0.0.1:<port>/app/) shown in its own window, with no
// address bar and no browser around it. It uses Microsoft's WebView2 control, which needs the WebView2 runtime
// (part of Windows 11 and of most up-to-date Windows 10). Where the runtime or the DLLs are missing the launcher
// falls back to the default browser (see TrayApp.OpenDictation), so this file must never be the only way in.
//
// Only AppWindow touches the WebView2 types. Everything that must work without them (checking whether the
// window can be used at all) lives in AppWindowSupport and uses plain files and the registry.
// C# 5 (the compiler that ships with Windows): no ?. and no string interpolation.
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.CompilerServices;
using System.Windows.Forms;
using Microsoft.Win32;

namespace BingliAssistant
{
    static class AppWindowSupport
    {
        // the "Evergreen" WebView2 runtime registers itself under this id
        const string RuntimeId = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}";

        public static readonly string[] RequiredFiles = new string[] {
            "Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll", Path.Combine("runtimes", Path.Combine("win-x64", Path.Combine("native", "WebView2Loader.dll")))
        };

        public static string RuntimeVersion()
        {
            string[] places = new string[] { @"SOFTWARE\Microsoft\EdgeUpdate\Clients\" + RuntimeId, @"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\" + RuntimeId };
            foreach (RegistryHive hive in new RegistryHive[] { RegistryHive.LocalMachine, RegistryHive.CurrentUser })
            {
                foreach (RegistryView view in new RegistryView[] { RegistryView.Registry64, RegistryView.Registry32 })
                {
                    try
                    {
                        using (RegistryKey root = RegistryKey.OpenBaseKey(hive, view))
                        {
                            foreach (string place in places)
                            {
                                using (RegistryKey key = root.OpenSubKey(place))
                                {
                                    string version = key == null ? null : key.GetValue("pv") as string;
                                    if (!string.IsNullOrEmpty(version) && version != "0.0.0.0") return version;
                                }
                            }
                        }
                    }
                    catch (Exception) { }
                }
            }
            return null;
        }

        // null when the window can be used, otherwise the reason it cannot (written to the launcher log)
        public static string WhyNot(string appDir)
        {
            if (!Environment.Is64BitProcess) return "not a 64-bit process";
            foreach (string file in RequiredFiles)
                if (!File.Exists(Path.Combine(appDir, file))) return "WebView2 component missing: " + file;
            if (RuntimeVersion() == null) return "the WebView2 runtime is not installed";
            return null;
        }
    }

    sealed class AppWindow : Form
    {
        readonly string baseUrl;
        readonly string userDataDir;
        readonly Action<string> log;
        readonly Action<string> failed;
        readonly Action ready;
        Microsoft.Web.WebView2.WinForms.WebView2 view;
        bool started;

        // `failed` is told why when the window cannot start, so that the caller can fall back to the browser
        public AppWindow(string baseUrl, string userDataDir, Icon icon, Action<string> log, Action<string> failed, Action ready)
        {
            log("window: creating");
            this.baseUrl = baseUrl.TrimEnd('/');
            this.userDataDir = userDataDir;
            this.log = log;
            this.failed = failed;
            this.ready = ready;
            Text = "病历助手";
            if (icon != null) Icon = icon;
            StartPosition = FormStartPosition.CenterScreen;
            Rectangle area = Screen.PrimaryScreen.WorkingArea;
            ClientSize = new Size(Math.Min(1360, area.Width - 40), Math.Min(900, area.Height - 60));
            MinimumSize = new Size(760, 520);
            BackColor = Color.FromArgb(243, 247, 245);
            log("window: creating the WebView2 control");
            view = new Microsoft.Web.WebView2.WinForms.WebView2();
            view.Dock = DockStyle.Fill;
            Controls.Add(view);
            Shown += delegate { log("window: shown"); Start(); };
            log("window: created");
        }

        async void Start()
        {
            try
            {
                Directory.CreateDirectory(userDataDir);
                log("window: creating the WebView2 environment");
                Microsoft.Web.WebView2.Core.CoreWebView2Environment env =
                    await Microsoft.Web.WebView2.Core.CoreWebView2Environment.CreateAsync(null, userDataDir, null);
                log("window: initialising the control");
                await view.EnsureCoreWebView2Async(env);
                Configure();
                started = true;
                view.CoreWebView2.NavigationCompleted += delegate (object sender, Microsoft.Web.WebView2.Core.CoreWebView2NavigationCompletedEventArgs e)
                {
                    log("window: page loaded, success=" + e.IsSuccess);
                    if (e.IsSuccess && ready != null) ready();
                };
                view.CoreWebView2.Navigate(baseUrl + "/app/");
                log("dictation window opened (WebView2 " + env.BrowserVersionString + ")");
            }
            catch (Exception ex)
            {
                log("the dictation window could not start: " + ex.GetType().Name + ": " + ex.Message);
                failed(ex.Message);
                Close();
            }
        }

        void Configure()
        {
            Microsoft.Web.WebView2.Core.CoreWebView2 core = view.CoreWebView2;
            core.Settings.AreDevToolsEnabled = false;
            core.Settings.IsStatusBarEnabled = false;
            core.Settings.AreBrowserAcceleratorKeysEnabled = false;     // no print / find / devtools shortcuts
            core.Settings.IsPasswordAutosaveEnabled = false;            // passwords are not offered for saving in a shared computer's profile
            core.Settings.IsGeneralAutofillEnabled = false;

            // the microphone for our own page only; every other permission is refused
            core.PermissionRequested += delegate (object sender, Microsoft.Web.WebView2.Core.CoreWebView2PermissionRequestedEventArgs e)
            {
                bool ours = e.Uri != null && e.Uri.StartsWith(baseUrl, StringComparison.OrdinalIgnoreCase);
                bool microphone = e.PermissionKind == Microsoft.Web.WebView2.Core.CoreWebView2PermissionKind.Microphone;
                e.State = ours && microphone ? Microsoft.Web.WebView2.Core.CoreWebView2PermissionState.Allow : Microsoft.Web.WebView2.Core.CoreWebView2PermissionState.Deny;
            };
            // nothing but the service's own address is shown here, and no extra windows
            core.NavigationStarting += delegate (object sender, Microsoft.Web.WebView2.Core.CoreWebView2NavigationStartingEventArgs e)
            {
                if (e.Uri != null && !e.Uri.StartsWith(baseUrl + "/", StringComparison.OrdinalIgnoreCase) && !e.Uri.StartsWith("blob:" + baseUrl, StringComparison.OrdinalIgnoreCase)
                    && !e.Uri.StartsWith("data:", StringComparison.OrdinalIgnoreCase))
                {
                    e.Cancel = true;
                    log("navigation refused: " + e.Uri);
                }
            };
            core.NewWindowRequested += delegate (object sender, Microsoft.Web.WebView2.Core.CoreWebView2NewWindowRequestedEventArgs e) { e.Handled = true; };
            core.ProcessFailed += delegate (object sender, Microsoft.Web.WebView2.Core.CoreWebView2ProcessFailedEventArgs e)
            {
                log("WebView2 process failed: " + e.ProcessFailedKind);
                try { core.Reload(); } catch (Exception) { }
            };
            core.DocumentTitleChanged += delegate { string t = core.DocumentTitle; Text = string.IsNullOrEmpty(t) ? "病历助手" : t; };
        }

        public void Bring()
        {
            if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
            Show();
            Activate();
            if (started)
            {
                try { if (view.CoreWebView2 != null && string.IsNullOrEmpty(view.CoreWebView2.Source)) view.CoreWebView2.Navigate(baseUrl + "/app/"); } catch (Exception) { }
            }
        }
    }

    static class AppWindowLauncher
    {
        // Kept apart from the caller: if the WebView2 files cannot be loaded, the exception surfaces here and not
        // while the caller is being compiled, where it could not be caught.
        [MethodImpl(MethodImplOptions.NoInlining)]
        public static Form Create(string baseUrl, string userDataDir, Icon icon, Action<string> log, Action<string> failed, Action ready)
        {
            return new AppWindow(baseUrl, userDataDir, icon, log, failed, ready);
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        public static void Bring(Form window)
        {
            ((AppWindow)window).Bring();
        }
    }
}
