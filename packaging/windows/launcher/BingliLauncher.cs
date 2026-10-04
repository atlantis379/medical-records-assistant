// BingliAssistant.exe - starts, watches and stops the local speech service for non-technical users.
//
//   BingliAssistant.exe            tray icon + start the service (what the desktop icon runs)
//   BingliAssistant.exe --start    start the service in the background and exit when it is ready
//   BingliAssistant.exe --stop     ask the service to stop (never kills unrelated programs)
//   BingliAssistant.exe --status   exit code 0 running, 1 not running, 2 port used by another program
//   BingliAssistant.exe --window   (started by the tray program) the dictation window, in a process of its own
//
//   options: --root <folder>  --port <n>  --no-browser  --no-preload  --model-wait <seconds>
//            --browser   open the dictation page in the default browser instead of the program's own window
//
// Build: scripts\build_windows_launcher.ps1 (uses the csc.exe that ships with Windows; C# 5).
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

namespace BingliAssistant
{
    enum ServiceState { NotRunning, Ours, Foreign }
    enum TrayState { Starting, Ready, Stopped, Blocked }

    sealed class Options
    {
        public string Root;
        public int Port = 8765;
        public bool NoBrowser;
        public bool NoPreload;
        public bool UseBrowser;                // never use the program's own window (WebView2)
        public int ModelWaitSeconds = 240;     // how long to wait for the recognition models to finish loading
        public string Command = "tray";

        public static Options Parse(string[] args)
        {
            Options o = new Options();
            o.Root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\', '/');
            for (int i = 0; i < args.Length; i++)
            {
                string a = args[i].ToLowerInvariant();
                if (a == "--root" && i + 1 < args.Length) o.Root = Path.GetFullPath(args[++i]).TrimEnd('\\', '/');
                else if (a == "--port" && i + 1 < args.Length) o.Port = int.Parse(args[++i]);
                else if (a == "--no-browser") o.NoBrowser = true;
                else if (a == "--no-preload") o.NoPreload = true;
                else if (a == "--browser") o.UseBrowser = true;
                else if (a == "--model-wait" && i + 1 < args.Length) o.ModelWaitSeconds = int.Parse(args[++i]);
                else if (a == "--start" || a == "--stop" || a == "--status" || a == "--window") o.Command = a.Substring(2);
            }
            return o;
        }
    }

    sealed class Service
    {
        public const string Identity = "\"service\":\"bingli-assistant\"";
        readonly Options options;
        public readonly string HomeDir;
        public readonly string LogDir;
        public readonly string ServiceLog;
        public readonly string LauncherLog;
        Process child;

        public Service(Options options)
        {
            this.options = options;
            string baseDir = Environment.GetEnvironmentVariable("BINGLI_HOME");
            if (string.IsNullOrEmpty(baseDir))
                baseDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "BingliAssistant");
            HomeDir = baseDir;
            LogDir = Path.Combine(baseDir, "logs");
            ServiceLog = Path.Combine(LogDir, "service.log");
            LauncherLog = Path.Combine(LogDir, "launcher.log");
        }

        public string BaseUrl { get { return "http://127.0.0.1:" + options.Port; } }

        public void Log(string message)
        {
            try
            {
                Directory.CreateDirectory(LogDir);
                File.AppendAllText(LauncherLog, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "  " + message + Environment.NewLine, Encoding.UTF8);
            }
            catch (Exception) { }
        }

        bool PortOpen()
        {
            try
            {
                using (TcpClient client = new TcpClient())
                {
                    IAsyncResult result = client.BeginConnect("127.0.0.1", options.Port, null, null);
                    if (!result.AsyncWaitHandle.WaitOne(600)) return false;
                    client.EndConnect(result);
                    return true;
                }
            }
            catch (Exception) { return false; }
        }

        // NotRunning: nothing listens. Ours: our service answered. Foreign: something else holds the port.
        public ServiceState Probe(out string body)
        {
            body = null;
            try
            {
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create(BaseUrl + "/service/status");
                request.Proxy = null;                       // hospital proxies must never see local traffic
                request.Timeout = 1500;
                request.ReadWriteTimeout = 1500;
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                using (StreamReader reader = new StreamReader(response.GetResponseStream(), Encoding.UTF8))
                    body = reader.ReadToEnd();
                return body.Contains(Identity) ? ServiceState.Ours : ServiceState.Foreign;
            }
            catch (WebException ex)
            {
                if (ex.Response != null) return ServiceState.Foreign;
                return PortOpen() ? ServiceState.Foreign : ServiceState.NotRunning;
            }
            catch (Exception) { return PortOpen() ? ServiceState.Foreign : ServiceState.NotRunning; }
        }

        public ServiceState Probe() { string ignored; return Probe(out ignored); }

        string PythonPath(out bool offlineLayout)
        {
            string bundled = Path.Combine(options.Root, "runtime", "python", "python.exe");
            offlineLayout = File.Exists(bundled);
            return offlineLayout ? bundled : Path.Combine(options.Root, ".venv", "Scripts", "python.exe");
        }

        void RotateLog()
        {
            try
            {
                FileInfo info = new FileInfo(ServiceLog);
                if (info.Exists && info.Length > 5 * 1024 * 1024)
                {
                    string old = Path.Combine(LogDir, "service.1.log");
                    if (File.Exists(old)) File.Delete(old);
                    File.Move(ServiceLog, old);
                }
            }
            catch (Exception) { }
        }

        public bool Start(out string error)
        {
            error = null;
            bool offline;
            string python = PythonPath(out offline);
            if (!File.Exists(python)) { error = "找不到 Python 运行环境：" + python; return false; }
            if (!File.Exists(Path.Combine(options.Root, "server", "app.py"))) { error = "找不到服务程序：" + Path.Combine(options.Root, "server", "app.py"); return false; }
            Directory.CreateDirectory(LogDir);
            RotateLog();
            File.AppendAllText(ServiceLog, Environment.NewLine + "==== " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " launcher starting service ====" + Environment.NewLine, Encoding.UTF8);

            // cmd redirects the output to a file, so the service does not depend on this program staying alive
            string commandLine = "\"" + Path.Combine(Environment.SystemDirectory, "cmd.exe") + "\" /c \"\"" + python +
                                 "\" -B -m uvicorn server.app:app --host 127.0.0.1 --port " + options.Port +
                                 " >> \"" + ServiceLog + "\" 2>&1\"";
            Dictionary<string, string> env = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (System.Collections.DictionaryEntry entry in Environment.GetEnvironmentVariables())
                env[(string)entry.Key] = (string)entry.Value;
            if (string.IsNullOrEmpty(Environment.GetEnvironmentVariable("ASR_DEVICE"))) env["ASR_DEVICE"] = "cpu";
            env["ASR_PRELOAD_STREAMING"] = options.NoPreload ? "0" : "1";
            env["ASR_PRELOAD_BATCH"] = options.NoPreload ? "0" : "1";   // otherwise the first dictation waits 40+ s for this model
            env["PYTHONUTF8"] = "1";
            env["PYTHONNOUSERSITE"] = "1";    // the service must use its own libraries, never the ones in the user's profile
            string cache = Path.Combine(options.Root, "models", "modelscope", "hub");
            if (offline && Directory.Exists(cache))
            {
                env["MODELSCOPE_CACHE"] = cache;
                env["MODELSCOPE_OFFLINE"] = "1";
                env["HF_HUB_OFFLINE"] = "1";
                env["TRANSFORMERS_OFFLINE"] = "1";
            }
            if (offline)
                env["PYTHONPATH"] = Path.Combine(options.Root, ".venv", "Lib", "site-packages") + ";" + options.Root;

            int pid;
            if (!Native.StartDetached(commandLine, options.Root, env, out pid, out error)) return false;
            try { child = Process.GetProcessById(pid); } catch (Exception) { child = null; }
            Log("service process started (cmd pid " + pid + ")");
            return true;
        }

        public bool ChildExited { get { return child != null && child.HasExited; } }

        public bool LastWaitTimedOut;          // WaitModelsLoaded gave up because of the time, not because loading failed

        string GetText(string path)
        {
            try
            {
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create(BaseUrl + path);
                request.Proxy = null;
                request.Timeout = 3000;
                request.ReadWriteTimeout = 3000;
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                using (StreamReader reader = new StreamReader(response.GetResponseStream(), Encoding.UTF8))
                    return reader.ReadToEnd();
            }
            catch (Exception) { return null; }
        }

        // The service answers within seconds but loads the recognition models in the background (30-60 s). Until
        // then the first dictation would stall, so "ready" should mean "models loaded". Returns true when they are
        // loaded (or when nothing was asked to preload); false, with the reason, when loading failed or took too long.
        public bool WaitModelsLoaded(int timeoutMs, out string error)
        {
            error = null;
            LastWaitTimedOut = false;
            if (options.NoPreload) return true;
            DateTime deadline = DateTime.UtcNow.AddMilliseconds(timeoutMs);
            while (DateTime.UtcNow < deadline)
            {
                string health = GetText("/health");
                if (health != null)
                {
                    bool batch = Regex.IsMatch(health, "\"model_loaded\":true");
                    bool streaming = Regex.IsMatch(health, "\"streaming_model_loaded\":true");
                    Match batchError = Regex.Match(health, "\"batch_model_error\":\"([^\"]*)\"");
                    Match streamingError = Regex.Match(health, "\"streaming_model_error\":\"([^\"]*)\"");
                    if (batchError.Success)
                    {
                        error = "语音识别模型加载失败：" + batchError.Groups[1].Value;
                        return false;
                    }
                    if (batch && (streaming || streamingError.Success))
                    {
                        if (streamingError.Success) Log("streaming model failed to load, batch recognition only: " + streamingError.Groups[1].Value);
                        return true;
                    }
                }
                Thread.Sleep(1000);
            }
            error = "语音模型加载超过 " + (timeoutMs / 1000) + " 秒仍未完成。" + LastLogLines(4);
            LastWaitTimedOut = true;
            return false;
        }

        // Waits until our service answers. Gives up if the process we started has died.
        public bool WaitReady(int timeoutMs, out string error)
        {
            error = null;
            DateTime deadline = DateTime.UtcNow.AddMilliseconds(timeoutMs);
            while (DateTime.UtcNow < deadline)
            {
                ServiceState state = Probe();
                if (state == ServiceState.Ours) return true;
                if (ChildExited)
                {
                    error = "服务启动后立即退出。" + LastLogLines(6);
                    return false;
                }
                Thread.Sleep(500);
            }
            error = "服务启动超时（超过 " + (timeoutMs / 1000) + " 秒）。" + LastLogLines(4);
            return false;
        }

        public string LastLogLines(int count)
        {
            try
            {
                string[] lines = File.ReadAllLines(ServiceLog, Encoding.UTF8);
                int from = Math.Max(0, lines.Length - count);
                return Environment.NewLine + string.Join(Environment.NewLine, lines, from, lines.Length - from);
            }
            catch (Exception) { return ""; }
        }

        bool RequestShutdown()
        {
            try
            {
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create(BaseUrl + "/service/shutdown");
                request.Method = "POST";
                request.Proxy = null;
                request.Timeout = 4000;
                request.ContentLength = 0;
                request.Headers["Origin"] = BaseUrl;
                request.Headers["X-Bingli-Control"] = "1";
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                    return (int)response.StatusCode == 200;
            }
            catch (Exception ex) { Log("shutdown request failed: " + ex.Message); return false; }
        }

        // Stops our service. A program that merely holds the port is never touched.
        public bool Stop(int timeoutMs)
        {
            string body;
            ServiceState state = Probe(out body);
            if (state != ServiceState.Ours) return state == ServiceState.NotRunning;
            RequestShutdown();
            DateTime deadline = DateTime.UtcNow.AddMilliseconds(timeoutMs);
            while (DateTime.UtcNow < deadline)
            {
                if (Probe() != ServiceState.Ours) return true;
                Thread.Sleep(300);
            }
            // The service did not exit by itself. Its identity has been verified, so end that process.
            Match m = Regex.Match(body ?? "", "\"pid\":(\\d+)");
            if (m.Success)
            {
                try
                {
                    Process.GetProcessById(int.Parse(m.Groups[1].Value)).Kill();
                    Log("service did not stop in time; process " + m.Groups[1].Value + " was ended");
                }
                catch (Exception ex) { Log("could not end service process: " + ex.Message); }
            }
            Thread.Sleep(800);
            return Probe() != ServiceState.Ours;
        }
    }

    static class Native
    {
        [DllImport("kernel32.dll")] public static extern bool AttachConsole(int processId);
        [DllImport("kernel32.dll")] static extern IntPtr GetStdHandle(int standardHandle);
        [DllImport("kernel32.dll", SetLastError = true)] static extern bool SetHandleInformation(IntPtr handle, int mask, int flags);

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        struct StartupInfo
        {
            public int cb;
            public string reserved, desktop, title;
            public int x, y, xSize, ySize, xCountChars, yCountChars, fillAttribute, flags;
            public short showWindow, reserved2;
            public IntPtr reserved2Ptr, stdInput, stdOutput, stdError;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct ProcessInformation
        {
            public IntPtr process, thread;
            public int processId, threadId;
        }

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        static extern bool CreateProcess(string application, StringBuilder commandLine, IntPtr processAttributes, IntPtr threadAttributes,
            bool inheritHandles, uint creationFlags, IntPtr environment, string currentDirectory, ref StartupInfo startup, out ProcessInformation info);

        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);

        // Starts a hidden process that inherits NO handles. A child normally inherits every inheritable handle of its
        // parent, including a pipe a caller gave us as stdout; the long-running service would then keep that pipe open
        // and anything waiting for our output (a batch file, the installer, a test) would wait until the service exits.
        public static bool StartDetached(string commandLine, string workingDirectory, Dictionary<string, string> environment, out int pid, out string error)
        {
            pid = 0;
            error = null;
            StringBuilder block = new StringBuilder();
            foreach (KeyValuePair<string, string> pair in environment) block.Append(pair.Key).Append('=').Append(pair.Value).Append('\0');
            block.Append('\0');
            IntPtr env = Marshal.StringToHGlobalUni(block.ToString());
            try
            {
                StartupInfo startup = new StartupInfo();
                startup.cb = Marshal.SizeOf(typeof(StartupInfo));
                ProcessInformation info;
                const uint CREATE_NO_WINDOW = 0x08000000, CREATE_UNICODE_ENVIRONMENT = 0x00000400, CREATE_NEW_PROCESS_GROUP = 0x00000200;
                bool ok = CreateProcess(null, new StringBuilder(commandLine), IntPtr.Zero, IntPtr.Zero, false,
                    CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT | CREATE_NEW_PROCESS_GROUP, env, workingDirectory, ref startup, out info);
                if (!ok)
                {
                    error = "无法启动服务（错误码 " + Marshal.GetLastWin32Error() + "）";
                    return false;
                }
                pid = info.processId;
                CloseHandle(info.thread);
                CloseHandle(info.process);
                return true;
            }
            finally { Marshal.FreeHGlobal(env); }
        }

        [DllImport("user32.dll", CharSet = CharSet.Auto)] public static extern bool DestroyIcon(IntPtr handle);
    }

    sealed class TrayApp : ApplicationContext
    {
        readonly Options options;
        readonly Service service;
        readonly NotifyIcon icon;
        readonly System.Windows.Forms.Timer timer;
        readonly Dictionary<TrayState, Icon> icons = new Dictionary<TrayState, Icon>();
        readonly ToolStripMenuItem header, open, statusItem, restart, start, stop, logs, quit;
        readonly EventWaitHandle showSignal;
        TrayState state = TrayState.Stopped;
        string versionText = "";
        bool busy;
        bool modelError;
        bool leaving;
        int probing;
        Process windowProcess;
        bool windowUnavailable;
        EventWaitHandle windowReady;
        const int WindowStartSeconds = 45;     // how long the window may take to show the page before the browser is used

        public const string SignalName = "Local\\BingliAssistantLauncher.Show";

        public TrayApp(Options options, EventWaitHandle showSignal)
        {
            this.options = options;
            this.showSignal = showSignal;
            service = new Service(options);
            icons[TrayState.Starting] = MakeIcon(Color.FromArgb(230, 170, 30));
            icons[TrayState.Ready] = MakeIcon(Color.FromArgb(18, 106, 86));
            icons[TrayState.Stopped] = MakeIcon(Color.FromArgb(170, 170, 170));
            icons[TrayState.Blocked] = MakeIcon(Color.FromArgb(190, 60, 55));

            header = new ToolStripMenuItem("病历助手"); header.Enabled = false;
            open = new ToolStripMenuItem("打开听写页面", null, delegate { OpenStatusPage(); });
            statusItem = new ToolStripMenuItem("查看服务状态", null, delegate { OpenStatusPage("/"); });
            start = new ToolStripMenuItem("启动服务", null, delegate { BeginStart(true); });
            restart = new ToolStripMenuItem("重启服务", null, delegate { BeginRestart(); });
            stop = new ToolStripMenuItem("停止服务并退出", null, delegate { BeginStopAndQuit(); });
            logs = new ToolStripMenuItem("打开日志文件夹", null, delegate { OpenLogs(); });
            quit = new ToolStripMenuItem("退出托盘（保持服务运行）", null, delegate { Leave(); });
            ContextMenuStrip menu = new ContextMenuStrip();
            menu.Items.AddRange(new ToolStripItem[] { header, new ToolStripSeparator(), open, statusItem, start, restart, stop, new ToolStripSeparator(), logs, quit });

            icon = new NotifyIcon();
            icon.ContextMenuStrip = menu;
            icon.Visible = true;
            icon.DoubleClick += delegate { OpenStatusPage(); };
            ApplyState(TrayState.Starting, "正在启动…");

            timer = new System.Windows.Forms.Timer();
            timer.Interval = 2000;
            timer.Tick += delegate { Poll(); };
            timer.Start();

            Thread watcher = new Thread(WatchForSecondLaunch);
            watcher.IsBackground = true;
            watcher.Start();

            BeginStart(!options.NoBrowser);
        }

        static Icon MakeIcon(Color color)
        {
            using (Bitmap bitmap = new Bitmap(16, 16))
            {
                using (Graphics g = Graphics.FromImage(bitmap))
                {
                    g.SmoothingMode = SmoothingMode.AntiAlias;
                    using (SolidBrush brush = new SolidBrush(color)) g.FillEllipse(brush, 1, 1, 13, 13);
                    using (Pen pen = new Pen(Color.FromArgb(90, 0, 0, 0))) g.DrawEllipse(pen, 1, 1, 13, 13);
                }
                IntPtr handle = bitmap.GetHicon();
                Icon temp = Icon.FromHandle(handle);
                Icon copy = (Icon)temp.Clone();
                temp.Dispose();
                Native.DestroyIcon(handle);
                return copy;
            }
        }

        void OnUi(Action action)
        {
            if (leaving) return;
            try
            {
                if (icon.ContextMenuStrip.InvokeRequired) icon.ContextMenuStrip.BeginInvoke(action);
                else action();
            }
            catch (Exception) { }
        }

        void ApplyState(TrayState newState, string text)
        {
            state = newState;
            icon.Icon = icons[newState];
            string tip = "病历助手：" + text + (versionText.Length > 0 && newState == TrayState.Ready ? "（" + versionText + "）" : "");
            icon.Text = tip.Length > 63 ? tip.Substring(0, 63) : tip;
            header.Text = "病历助手：" + text;
            bool ready = newState == TrayState.Ready;
            open.Enabled = ready;
            restart.Enabled = ready;
            start.Enabled = newState == TrayState.Stopped;
            stop.Enabled = !busy || ready;
        }

        void Balloon(string title, string text, ToolTipIcon kind)
        {
            try { icon.ShowBalloonTip(6000, title, text, kind); } catch (Exception) { }
        }

        // the dictation page is served by the service itself (no browser extension to install)
        void OpenStatusPage() { OpenStatusPage("/app/"); }

        void OpenStatusPage(string path)
        {
            if (state != TrayState.Ready) return;
            if (path == "/app/" && TryOpenWindow()) return;
            try { Process.Start(service.BaseUrl + path); } catch (Exception ex) { service.Log("could not open browser: " + ex.Message); }
        }

        // The dictation page in the program's own window; false means "use the browser instead" (no WebView2 runtime,
        // missing files, the --browser option, or an earlier failure in this run).
        // The window runs in a process of its own (--window): if WebView2 hangs or crashes, the tray and the service
        // are not affected, and a window that does not show the page in time is ended and replaced by the browser.
        bool TryOpenWindow()
        {
            if (options.UseBrowser || windowUnavailable) return false;
            try
            {
                if (windowProcess != null && !windowProcess.HasExited)
                {
                    try { EventWaitHandle.OpenExisting(Program.WindowShowName(options.Port)).Set(); } catch (Exception) { }
                    return true;
                }
                string why = AppWindowSupport.WhyNot(AppDomain.CurrentDomain.BaseDirectory);
                if (why != null)
                {
                    windowUnavailable = true;
                    service.Log("the program's own window is not used (" + why + "); the browser is used instead");
                    return false;
                }
                if (windowReady == null) windowReady = new EventWaitHandle(false, EventResetMode.ManualReset, Program.WindowReadyName(options.Port));
                windowReady.Reset();
                ProcessStartInfo info = new ProcessStartInfo(Application.ExecutablePath,
                    "--window --port " + options.Port + " --root \"" + options.Root + "\"");
                info.UseShellExecute = false;
                Process started = Process.Start(info);
                windowProcess = started;
                service.Log("dictation window process started (pid " + started.Id + ")");
                ThreadPool.QueueUserWorkItem(delegate { WatchWindow(started); });
                return true;
            }
            catch (Exception ex)
            {
                windowUnavailable = true;
                service.Log("the program's own window failed (" + ex.GetType().Name + ": " + ex.Message + "); the browser is used instead");
                return false;
            }
        }

        void WatchWindow(Process window)
        {
            DateTime deadline = DateTime.UtcNow.AddSeconds(WindowStartSeconds);
            while (DateTime.UtcNow < deadline && !leaving)
            {
                try
                {
                    if (windowReady.WaitOne(300)) return;                 // the page is showing
                    if (window.HasExited)
                    {
                        if (window.ExitCode == 0) return;                 // for example: a window was already open
                        service.Log("the dictation window ended with code " + window.ExitCode + "; the browser is used instead");
                        FallBackToBrowser("exit code " + window.ExitCode);
                        return;
                    }
                }
                catch (Exception) { return; }
            }
            if (leaving) return;
            service.Log("the dictation window did not show the page within " + WindowStartSeconds + " seconds; it is ended and the browser is used instead");
            try { if (!window.HasExited) window.Kill(); } catch (Exception) { }
            FallBackToBrowser("timeout");
        }

        void FallBackToBrowser(string why)
        {
            windowUnavailable = true;
            OnUi(delegate
            {
                try { Process.Start(service.BaseUrl + "/app/"); } catch (Exception ex) { service.Log("could not open browser: " + ex.Message); }
            });
        }

        void OpenLogs()
        {
            try { Directory.CreateDirectory(service.LogDir); Process.Start("explorer.exe", "\"" + service.LogDir + "\""); } catch (Exception) { }
        }

        // ---- starting
        void BeginStart(bool openBrowser)
        {
            if (busy) return;
            busy = true;
            modelError = false;
            OnUi(delegate { ApplyState(TrayState.Starting, "正在启动…"); });
            ThreadPool.QueueUserWorkItem(delegate { StartWorker(openBrowser); });
        }

        void StartWorker(bool openBrowser)
        {
            string body;
            ServiceState found = service.Probe(out body);
            if (found == ServiceState.Foreign)
            {
                busy = false;
                OnUi(delegate
                {
                    ApplyState(TrayState.Blocked, "端口 " + options.Port + " 被其他程序占用");
                    Balloon("病历助手无法启动", "端口 " + options.Port + " 已被其他程序占用，病历助手没有去结束它。请关闭占用该端口的程序，或联系技术支持。", ToolTipIcon.Error);
                });
                return;
            }
            string error = null;
            bool ok = true;
            if (found == ServiceState.NotRunning)
            {
                ok = service.Start(out error) && service.WaitReady(240000, out error);
            }
            bool modelsFailed = false;
            if (ok)
            {
                OnUi(delegate { ApplyState(TrayState.Starting, "正在加载语音模型…"); });
                bool loaded = service.WaitModelsLoaded(options.ModelWaitSeconds * 1000, out error);
                if (!loaded && service.LastWaitTimedOut)
                {
                    // a slow or busy computer: loading is probably still going on, so keep waiting instead of reporting a failure
                    service.Log("the models are still loading after " + options.ModelWaitSeconds + " seconds; waiting longer");
                    OnUi(delegate { ApplyState(TrayState.Starting, "语音模型仍在加载（电脑较慢），请耐心等待…"); });
                    loaded = service.WaitModelsLoaded(1800 * 1000, out error);
                }
                if (!loaded)
                {
                    ok = false;
                    modelsFailed = true;
                }
            }
            busy = false;
            if (modelsFailed)
            {
                modelError = true;
                service.Log("models did not load: " + error);
                OnUi(delegate
                {
                    ApplyState(TrayState.Blocked, "语音模型未能加载");
                    Balloon("病历助手：语音模型未能加载", error, ToolTipIcon.Error);
                });
                return;
            }
            if (ok)
            {
                string status; service.Probe(out status);
                Match v = Regex.Match(status ?? "", "\"version\":\"([^\"]*)\"");
                OnUi(delegate
                {
                    versionText = v.Success ? "版本 " + v.Groups[1].Value : "";
                    ApplyState(TrayState.Ready, "已就绪");
                    Balloon("病历助手已就绪", "请点击浏览器右上角的“病历助手”图标开始听写。用完后在托盘图标上选择“停止服务并退出”。", ToolTipIcon.Info);
                    if (openBrowser) OpenStatusPage();
                });
            }
            else
            {
                service.Log("start failed: " + error);
                OnUi(delegate
                {
                    ApplyState(TrayState.Stopped, "启动失败");
                    Balloon("病历助手启动失败", error, ToolTipIcon.Error);
                });
            }
        }

        // ---- watching
        void Poll()
        {
            if (busy || leaving || Interlocked.Exchange(ref probing, 1) == 1) return;
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    ServiceState found = service.Probe();
                    OnUi(delegate
                    {
                        if (busy || leaving) return;
                        if (found == ServiceState.Ours && state != TrayState.Ready && !modelError) ApplyState(TrayState.Ready, "已就绪");
                        else if (found == ServiceState.NotRunning && state == TrayState.Ready)
                        {
                            ApplyState(TrayState.Stopped, "服务已停止");
                            Balloon("病历助手服务已停止", "需要时可在托盘图标上选择“启动服务”，或再次双击桌面图标。", ToolTipIcon.Info);
                        }
                    });
                }
                finally { Interlocked.Exchange(ref probing, 0); }
            });
        }

        void WatchForSecondLaunch()
        {
            while (!leaving)
            {
                try
                {
                    if (!showSignal.WaitOne(1000)) continue;
                    OnUi(delegate
                    {
                        if (state == TrayState.Ready) { if (!options.NoBrowser) OpenStatusPage(); }
                        else if (state == TrayState.Stopped || state == TrayState.Blocked) BeginStart(!options.NoBrowser);
                    });
                }
                catch (Exception) { return; }
            }
        }

        // ---- restarting / stopping
        void BeginRestart()
        {
            if (busy) return;
            busy = true;
            OnUi(delegate { ApplyState(TrayState.Starting, "正在重启…"); });
            ThreadPool.QueueUserWorkItem(delegate
            {
                service.Stop(15000);
                busy = false;
                StartWorker(false);
            });
        }

        void BeginStopAndQuit()
        {
            if (leaving) return;
            busy = true;
            OnUi(delegate { ApplyState(TrayState.Starting, "正在停止…"); });
            ThreadPool.QueueUserWorkItem(delegate
            {
                bool stopped = service.Stop(15000);
                service.Log(stopped ? "service stopped by the user; launcher exiting" : "service could not be stopped");
                if (!stopped)
                {
                    busy = false;
                    OnUi(delegate { ApplyState(TrayState.Blocked, "无法停止服务"); Balloon("病历助手", "服务没有停止，请打开日志文件夹查看原因。", ToolTipIcon.Error); });
                    return;
                }
                OnUi(delegate { Leave(); });
            });
        }

        void Leave()
        {
            leaving = true;
            try { if (windowProcess != null && !windowProcess.HasExited) windowProcess.Kill(); } catch (Exception) { }
            try { timer.Stop(); icon.Visible = false; icon.Dispose(); } catch (Exception) { }
            ExitThread();
        }
    }

    static class Program
    {
        public static string WindowShowName(int port) { return "Local\\BingliAssistantWindow.Show" + port; }
        public static string WindowReadyName(int port) { return "Local\\BingliAssistantWindow.Ready" + port; }

        [STAThread]                       // WinForms and WebView2 need a single-threaded apartment
        static int Main(string[] args)
        {
            Options options = Options.Parse(args);
            if (options.Command == "window") return RunWindow(options);
            if (options.Command != "tray") return RunCommand(options);

            bool createdNew;
            using (Mutex mutex = new Mutex(true, "Local\\BingliAssistantLauncher", out createdNew))
            {
                EventWaitHandle signal = new EventWaitHandle(false, EventResetMode.AutoReset, TrayApp.SignalName);
                if (!createdNew)
                {
                    signal.Set();     // a launcher is already running: ask it to show the status page
                    return 0;
                }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new TrayApp(options, signal));
                GC.KeepAlive(mutex);
            }
            return 0;
        }

        // The dictation window, in its own process. Exit code: 0 closed normally (or already open), 3 could not start.
        static int RunWindow(Options options)
        {
            Service service = new Service(options);
            bool created;
            using (Mutex mutex = new Mutex(true, "Local\\BingliAssistantWindow" + options.Port, out created))
            {
                EventWaitHandle show = new EventWaitHandle(false, EventResetMode.AutoReset, WindowShowName(options.Port));
                if (!created) { show.Set(); return 0; }
                EventWaitHandle ready = new EventWaitHandle(false, EventResetMode.ManualReset, WindowReadyName(options.Port));
                int code = 0;
                try
                {
                    Application.EnableVisualStyles();
                    Application.SetCompatibleTextRenderingDefault(false);
                    Form form = AppWindowLauncher.Create(service.BaseUrl, Path.Combine(service.HomeDir, "webview"), null, service.Log,
                        delegate (string why) { code = 3; }, delegate { ready.Set(); });
                    Thread watcher = new Thread(delegate ()
                    {
                        while (true)
                        {
                            try
                            {
                                show.WaitOne();
                                form.BeginInvoke((MethodInvoker)delegate { AppWindowLauncher.Bring(form); });
                            }
                            catch (Exception) { return; }
                        }
                    });
                    watcher.IsBackground = true;
                    watcher.Start();
                    Application.Run(form);
                }
                catch (Exception ex)
                {
                    service.Log("dictation window process failed: " + ex.GetType().Name + ": " + ex.Message);
                    return 3;
                }
                GC.KeepAlive(mutex);
                return code;
            }
        }

        static int RunCommand(Options options)
        {
            Native.AttachConsole(-1);
            Service service = new Service(options);
            string body;
            ServiceState state = service.Probe(out body);
            switch (options.Command)
            {
                case "status":
                    if (state == ServiceState.Ours) { Console.WriteLine("running " + body); return 0; }
                    if (state == ServiceState.Foreign) { Console.WriteLine("port " + options.Port + " is used by another program"); return 2; }
                    Console.WriteLine("not running");
                    return 1;
                case "stop":
                    if (state == ServiceState.Foreign) { Console.WriteLine("port " + options.Port + " is used by another program; nothing was stopped"); return 2; }
                    if (state == ServiceState.NotRunning) { Console.WriteLine("not running"); return 0; }
                    bool stopped = service.Stop(15000);
                    Console.WriteLine(stopped ? "stopped" : "could not stop the service");
                    return stopped ? 0 : 1;
                default: // start
                    if (state == ServiceState.Foreign) { Console.WriteLine("port " + options.Port + " is used by another program"); return 2; }
                    string error = null;
                    bool alreadyRunning = state == ServiceState.Ours;
                    if (alreadyRunning || (service.Start(out error) && service.WaitReady(240000, out error)))
                    {
                        if (service.WaitModelsLoaded(options.ModelWaitSeconds * 1000, out error))
                        {
                            Console.WriteLine(alreadyRunning ? "already running" : "ready");
                            return 0;
                        }
                    }
                    Console.WriteLine("failed: " + error);
                    return 1;
            }
        }
    }
}
