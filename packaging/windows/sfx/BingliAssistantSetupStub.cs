using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;

namespace BingliAssistantSetup
{
    internal static class Program
    {
        private static readonly byte[] Magic = Encoding.ASCII.GetBytes("BLASFX1!");
        private const int FooterLength = 48;
        private const string ProductName = "\u75c5\u5386\u52a9\u624b";
        private const string DefaultInstallFolder = "BingliAssistant";

        private sealed class PayloadInfo
        {
            public string ExePath;
            public long PayloadOffset;
            public long PayloadLength;
            public byte[] Sha256;
        }

        private sealed class BoundedReadStream : Stream
        {
            private readonly FileStream _base;
            private readonly long _start;
            private readonly long _length;
            private long _position;

            public BoundedReadStream(string path, long start, long length)
            {
                _base = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
                _start = start;
                _length = length;
                _position = 0;
                _base.Position = _start;
            }

            public override bool CanRead { get { return true; } }
            public override bool CanSeek { get { return true; } }
            public override bool CanWrite { get { return false; } }
            public override long Length { get { return _length; } }
            public override long Position
            {
                get { return _position; }
                set { Seek(value, SeekOrigin.Begin); }
            }

            public override int Read(byte[] buffer, int offset, int count)
            {
                if (_position >= _length) return 0;
                long remaining = _length - _position;
                if (count > remaining) count = (int)Math.Min(count, remaining);
                _base.Position = _start + _position;
                int read = _base.Read(buffer, offset, count);
                _position += read;
                return read;
            }

            public override long Seek(long offset, SeekOrigin origin)
            {
                long target;
                switch (origin)
                {
                    case SeekOrigin.Begin: target = offset; break;
                    case SeekOrigin.Current: target = _position + offset; break;
                    case SeekOrigin.End: target = _length + offset; break;
                    default: throw new ArgumentOutOfRangeException("origin");
                }
                if (target < 0) throw new IOException("Cannot seek before payload start.");
                if (target > _length) throw new IOException("Cannot seek beyond payload end.");
                _position = target;
                return _position;
            }

            public override void Flush() { }
            public override void SetLength(long value) { throw new NotSupportedException(); }
            public override void Write(byte[] buffer, int offset, int count) { throw new NotSupportedException(); }
            protected override void Dispose(bool disposing)
            {
                if (disposing) _base.Dispose();
                base.Dispose(disposing);
            }
        }

        private static int Main(string[] args)
        {
            Console.OutputEncoding = Encoding.UTF8;
            try
            {
                var info = ReadPayloadInfo();
                var options = ParseArgs(args);

                if (options.ContainsKey("help") || options.ContainsKey("h"))
                {
                    PrintHelp();
                    return 0;
                }

                if (options.ContainsKey("info"))
                {
                    PrintInfo(info);
                    return 0;
                }

                if (options.ContainsKey("verify-only"))
                {
                    VerifyPayload(info, true);
                    return 0;
                }

                if (options.ContainsKey("smoke-test"))
                {
                    VerifyPayload(info, true);
                    SmokeTestZip(info);
                    return 0;
                }

                string target = GetOption(options, "target", Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), DefaultInstallFolder));
                bool yes = options.ContainsKey("yes") || options.ContainsKey("y");
                bool noLaunch = options.ContainsKey("no-launch");

                Console.WriteLine(ProductName + " Windows setup");
                Console.WriteLine("Install directory: " + target);
                Console.WriteLine("Payload size: " + FormatBytes(info.PayloadLength));
                Console.WriteLine();

                if (!yes)
                {
                    Console.Write("Continue installation? [Y/n] ");
                    string answer = Console.ReadLine();
                    if (!String.IsNullOrWhiteSpace(answer) && answer.Trim().ToLowerInvariant().StartsWith("n"))
                    {
                        Console.WriteLine("Canceled.");
                        return 2;
                    }
                }

                VerifyPayload(info, false);
                ExtractPayload(info, target);
                string installedRoot = FindInstalledRoot(target);
                CreateShortcuts(installedRoot, GetOption(options, "shortcut-root", null));

                Console.WriteLine();
                Console.WriteLine("Installation completed.");
                Console.WriteLine("Installed folder: " + installedRoot);
                Console.WriteLine("Next steps:");
                Console.WriteLine("1. Double-click the desktop shortcut (or BingliAssistant.exe). A tray icon shows the service state.");
                Console.WriteLine("2. Open http://127.0.0.1:8765/health and confirm status: ok.");
                Console.WriteLine("3. On the first start, create the administrator account in the window that opens. No browser extension is needed.");
                Console.WriteLine();

                if (!noLaunch)
                {
                    LaunchStartScript(installedRoot);
                }

                if (!yes)
                {
                    Console.WriteLine("Press Enter to exit.");
                    Console.ReadLine();
                }
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("Setup failed: " + ex.Message);
                Console.Error.WriteLine(ex.ToString());
                Console.Error.WriteLine("Press Enter to exit.");
                try { Console.ReadLine(); } catch { }
                return 1;
            }
        }

        private static Dictionary<string, string> ParseArgs(string[] args)
        {
            var map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < args.Length; i++)
            {
                string a = args[i];
                if (!a.StartsWith("--")) continue;
                string key = a.Substring(2);
                string value = "true";
                int eq = key.IndexOf('=');
                if (eq >= 0)
                {
                    value = key.Substring(eq + 1);
                    key = key.Substring(0, eq);
                }
                else if (i + 1 < args.Length && !args[i + 1].StartsWith("--"))
                {
                    value = args[++i];
                }
                map[key] = value;
            }
            return map;
        }

        private static string GetOption(Dictionary<string, string> options, string key, string fallback)
        {
            string value;
            if (options.TryGetValue(key, out value)) return value;
            return fallback;
        }

        private static PayloadInfo ReadPayloadInfo()
        {
            string exePath = Assembly.GetExecutingAssembly().Location;
            var file = new FileInfo(exePath);
            if (file.Length < FooterLength) throw new InvalidOperationException("Installer payload footer is missing.");

            byte[] footer = new byte[FooterLength];
            using (var fs = new FileStream(exePath, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                fs.Position = file.Length - FooterLength;
                ReadExactly(fs, footer, 0, FooterLength);
            }

            for (int i = 0; i < Magic.Length; i++)
            {
                if (footer[i] != Magic[i]) throw new InvalidOperationException("Installer payload magic is invalid.");
            }

            long payloadLength = BitConverter.ToInt64(footer, 8);
            if (payloadLength <= 0 || payloadLength > file.Length - FooterLength)
                throw new InvalidOperationException("Installer payload length is invalid.");

            byte[] hash = new byte[32];
            Buffer.BlockCopy(footer, 16, hash, 0, 32);
            return new PayloadInfo
            {
                ExePath = exePath,
                PayloadOffset = file.Length - FooterLength - payloadLength,
                PayloadLength = payloadLength,
                Sha256 = hash
            };
        }

        private static void ReadExactly(Stream stream, byte[] buffer, int offset, int count)
        {
            int total = 0;
            while (total < count)
            {
                int read = stream.Read(buffer, offset + total, count - total);
                if (read <= 0) throw new EndOfStreamException();
                total += read;
            }
        }

        private static void VerifyPayload(PayloadInfo info, bool verbose)
        {
            Console.WriteLine("Verifying embedded package...");
            using (var stream = new BoundedReadStream(info.ExePath, info.PayloadOffset, info.PayloadLength))
            using (var sha = SHA256.Create())
            {
                byte[] actual = sha.ComputeHash(stream);
                if (!BytesEqual(actual, info.Sha256))
                    throw new InvalidOperationException("Embedded package SHA256 mismatch. The installer may be corrupted.");
                if (verbose) Console.WriteLine("SHA256: " + ToHex(actual));
            }
        }

        private static void SmokeTestZip(PayloadInfo info)
        {
            Console.WriteLine("Opening embedded ZIP package...");
            using (var stream = new BoundedReadStream(info.ExePath, info.PayloadOffset, info.PayloadLength))
            using (var archive = new ZipArchive(stream, ZipArchiveMode.Read))
            {
                string[] required = new[]
                {
                    "/BingliAssistant.exe",
                    "/start_server_offline.bat",
                    "/extension/manifest.json",
                    "/server/app.py",
                    "/package-manifest.json",
                    "/models/modelscope/hub/models/iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch/",
                    "/models/modelscope/hub/models/iic/speech_fsmn_vad_zh-cn-16k-common-pytorch/"
                };
                foreach (string suffix in required)
                {
                    bool ok = archive.Entries.Any(e => NormalizeEntry(e.FullName).Contains(suffix));
                    if (!ok) throw new InvalidOperationException("Missing required package content: " + suffix);
                }
                Console.WriteLine("Smoke test OK. Entries: " + archive.Entries.Count);
            }
        }

        private static string NormalizeEntry(string name)
        {
            return "/" + name.Replace('\\', '/').TrimStart('/');
        }

        private static void ExtractPayload(PayloadInfo info, string target)
        {
            Directory.CreateDirectory(target);
            Console.WriteLine("Extracting files. This can take several minutes...");
            string targetFull = Path.GetFullPath(target).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
            int count = 0;
            using (var stream = new BoundedReadStream(info.ExePath, info.PayloadOffset, info.PayloadLength))
            using (var archive = new ZipArchive(stream, ZipArchiveMode.Read))
            {
                foreach (var entry in archive.Entries)
                {
                    string destination = Path.GetFullPath(Path.Combine(target, entry.FullName));
                    if (!destination.StartsWith(targetFull, StringComparison.OrdinalIgnoreCase))
                        throw new InvalidOperationException("Unsafe ZIP entry path: " + entry.FullName);

                    if (String.IsNullOrEmpty(entry.Name))
                    {
                        Directory.CreateDirectory(destination);
                    }
                    else
                    {
                        string dir = Path.GetDirectoryName(destination);
                        if (!String.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
                        using (var input = entry.Open())
                        using (var output = new FileStream(destination, FileMode.Create, FileAccess.Write, FileShare.None))
                        {
                            input.CopyTo(output);
                        }
                    }
                    count++;
                    if (count % 1000 == 0) Console.WriteLine("Extracted entries: " + count);
                }
            }
            Console.WriteLine("Extracted entries: " + count);
        }

        private static string FindInstalledRoot(string target)
        {
            var candidates = Directory.GetDirectories(target, "bingli-assistant-v*-offline")
                .OrderByDescending(d => Directory.GetLastWriteTimeUtc(d)).ToList();
            if (candidates.Count > 0) return candidates[0];
            return target;
        }

        // Desktop and Start-menu entries start the tray launcher: no console window, and a stop entry.
        // shortcutRoot (--shortcut-root) puts the shortcuts under that folder instead of the real desktop and Start menu
        private static void CreateShortcuts(string installedRoot, string shortcutRoot)
        {
            try
            {
                string launcher = Path.Combine(installedRoot, "BingliAssistant.exe");
                if (!File.Exists(launcher)) return;
                string desktop = shortcutRoot == null ? Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory) : Path.Combine(shortcutRoot, "Desktop");
                string programsRoot = shortcutRoot == null ? Environment.GetFolderPath(Environment.SpecialFolder.Programs) : Path.Combine(shortcutRoot, "Programs");
                Directory.CreateDirectory(desktop);
                string programs = Path.Combine(programsRoot, ProductName);
                Directory.CreateDirectory(programs);
                string stopName = "\u505c\u6b62" + ProductName + "\u670d\u52a1";
                SaveShortcut(Path.Combine(desktop, ProductName + ".lnk"), launcher, "", installedRoot, ProductName);
                SaveShortcut(Path.Combine(programs, ProductName + ".lnk"), launcher, "", installedRoot, ProductName);
                SaveShortcut(Path.Combine(programs, stopName + ".lnk"), launcher, "--stop", installedRoot, stopName);
                Console.WriteLine("Shortcuts created: desktop and Start menu (" + programs + ")");
            }
            catch (Exception ex)
            {
                Console.WriteLine("Shortcut creation skipped: " + ex.Message);
            }
        }

        private static void SaveShortcut(string path, string target, string arguments, string workingDirectory, string description)
        {
            Type shellType = Type.GetTypeFromProgID("WScript.Shell");
            if (shellType == null) return;
            object shell = Activator.CreateInstance(shellType);
            object shortcut = shellType.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { path });
            Type shortcutType = shortcut.GetType();
            shortcutType.InvokeMember("TargetPath", BindingFlags.SetProperty, null, shortcut, new object[] { target });
            shortcutType.InvokeMember("Arguments", BindingFlags.SetProperty, null, shortcut, new object[] { arguments });
            shortcutType.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, shortcut, new object[] { workingDirectory });
            shortcutType.InvokeMember("Description", BindingFlags.SetProperty, null, shortcut, new object[] { description });
            shortcutType.InvokeMember("Save", BindingFlags.InvokeMethod, null, shortcut, null);
        }

        private static void LaunchStartScript(string installedRoot)
        {
            string launcher = Path.Combine(installedRoot, "BingliAssistant.exe");
            if (!File.Exists(launcher)) return;
            Console.Write("Start the local service now? [Y/n] ");
            string answer = Console.ReadLine();
            if (!String.IsNullOrWhiteSpace(answer) && answer.Trim().ToLowerInvariant().StartsWith("n")) return;
            Process.Start(new ProcessStartInfo
            {
                FileName = launcher,
                WorkingDirectory = installedRoot,
                UseShellExecute = false
            });
        }

        private static void PrintInfo(PayloadInfo info)
        {
            Console.WriteLine(ProductName + " setup package");
            Console.WriteLine("EXE: " + info.ExePath);
            Console.WriteLine("Payload offset: " + info.PayloadOffset);
            Console.WriteLine("Payload size: " + FormatBytes(info.PayloadLength));
            Console.WriteLine("Payload SHA256: " + ToHex(info.Sha256));
        }

        private static void PrintHelp()
        {
            Console.WriteLine(ProductName + " Windows setup");
            Console.WriteLine("Usage:");
            Console.WriteLine("  setup.exe [--target <directory>] [--yes] [--no-launch] [--shortcut-root <directory>]");
            Console.WriteLine("  setup.exe --info");
            Console.WriteLine("  setup.exe --verify-only");
            Console.WriteLine("  setup.exe --smoke-test");
        }

        private static bool BytesEqual(byte[] a, byte[] b)
        {
            if (a == null || b == null || a.Length != b.Length) return false;
            int diff = 0;
            for (int i = 0; i < a.Length; i++) diff |= a[i] ^ b[i];
            return diff == 0;
        }

        private static string ToHex(byte[] bytes)
        {
            var sb = new StringBuilder(bytes.Length * 2);
            foreach (byte b in bytes) sb.Append(b.ToString("X2"));
            return sb.ToString();
        }

        private static string FormatBytes(long bytes)
        {
            double value = bytes;
            string[] units = { "B", "KB", "MB", "GB" };
            int idx = 0;
            while (value >= 1024 && idx < units.Length - 1)
            {
                value /= 1024;
                idx++;
            }
            return value.ToString("0.##") + " " + units[idx];
        }
    }
}
