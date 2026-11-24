using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Threading.Tasks;
using System.Windows.Forms;

class Setup {
    static string Extract(string parent) {
        Directory.CreateDirectory(parent);
        string stage = Path.Combine(parent, Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(stage);
        using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"))
        using (var archive = new ZipArchive(stream, ZipArchiveMode.Read)) {
            foreach (var entry in archive.Entries) {
                string path = Path.GetFullPath(Path.Combine(stage, entry.FullName));
                if (!path.StartsWith(stage + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("Unexpected archive path.");
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                entry.ExtractToFile(path);
            }
        }
        return Path.Combine(stage, "ThortClip.exe");
    }
    static void Activate(string exe) {
        dynamic shell=Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell"));
        dynamic link=shell.CreateShortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs),"ThortClip.lnk"));
        link.TargetPath=exe; link.WorkingDirectory=Path.GetDirectoryName(exe); link.Save();
        string oldShortcut = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "Thot Clip.lnk");
        if (File.Exists(oldShortcut)) {
            dynamic oldLink = shell.CreateShortcut(oldShortcut);
            string target = oldLink.TargetPath;
            string oldRoot = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Thot Clip") + Path.DirectorySeparatorChar;
            if (target.StartsWith(oldRoot, StringComparison.OrdinalIgnoreCase) &&
                String.Equals(Path.GetFileName(target), "ThotClip.exe", StringComparison.OrdinalIgnoreCase))
                File.Delete(oldShortcut);
        }
        Process.Start(exe);
    }
    [STAThread]
    static int Main(string[] args) {
        if (args.Length == 2 && args[0] == "/extract") {
            Extract(Path.GetFullPath(args[1]));
            return 0;
        }
        if (args.Length == 2 && args[0] == "/update") {
            try {
                int pid = int.Parse(args[1]);
                try {
                    using (var old = Process.GetProcessById(pid))
                        if (!old.WaitForExit(120000)) throw new IOException("ThortClip is still finishing work.");
                } catch (ArgumentException) { /* The previous instance has exited. */ }
                Activate(Extract(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                                              "Programs", "ThortClip")));
                return 0;
            } catch (Exception ex) {
                MessageBox.Show(ex.Message, "ThortClip update");
                return 1;
            }
        }
        Application.EnableVisualStyles();
        var form = new Form { Text="Install ThortClip", Width=410, Height=155,
            StartPosition=FormStartPosition.CenterScreen, MaximizeBox=false, FormBorderStyle=FormBorderStyle.FixedDialog };
        var label = new Label { Left=18, Top=16, Width=365, Height=42,
            Text="Install ThortClip for this Windows account and add a Start menu shortcut." };
        var button = new Button { Left=267, Top=64, Width=106, Text="Install" };
        form.Icon=System.Drawing.Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        form.Controls.Add(label); form.Controls.Add(button);
        button.Click += async delegate {
            button.Enabled=false; form.ControlBox=false; label.Text="Installing the application and media tools…";
            try {
                var parent=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Programs","ThortClip");
                string exe=await Task.Run(() => Extract(parent));
                Activate(exe); form.Close();
            } catch(Exception ex) {
                label.Text=ex.Message; button.Enabled=true; form.ControlBox=true;
            }
        };
        Application.Run(form); return 0;
    }
}
