'''
Portable Git Assistant

Layout (working dir is a child folder of the script folder):

pygit/
  github_helper.py    this script
  folder1/            working dir (autodetected: 1st child with folder1.url)
  folder1.url         text file containing the repository address
  t.txt / t.bin       access token (plain or encrypted)
  libs/               pip packages (auto-installed, portable)
  portable-git/       portable Git (auto-downloaded on Windows)

Buttons: clone, fetch, commit, push, apply patch, make HEAD main,
encrypt token.
A .patch/.diff file can be dropped anywhere in the window.
'''

import base64
import hashlib
import json
import os
import platform
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import urllib.request

from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

# Do not leave bytecode caches in the portable application folder.
sys.dont_write_bytecode = True

# cryptography and tkinterdnd2 are imported only after
# bootstrap_dependencies() has run.

# ------------------------------------------------------------
# User configuration
# ------------------------------------------------------------

# Default Git identity for commits. Edit these or use the
# input fields in the window. Passed per-commit via "-c",
# so nothing is ever written to Git config files.
GIT_NAME = "user1"
GIT_EMAIL = "user1@users.noreply.github.com"

# Branch names used by the "Make HEAD main" button.
MAIN_BRANCH = "main"
OLD_BRANCH = "old"

# ------------------------------------------------------------
# Paths and constants
# ------------------------------------------------------------

APP_DIR = Path(__file__).resolve().parent
TOKEN_TEXT_FILE = APP_DIR / "t.txt"
TOKEN_ENCRYPTED_FILE = APP_DIR / "t.bin"
PORTABLE_GIT_DIR = APP_DIR / "portable-git"
LIBS_DIR = APP_DIR / "libs"

# App infrastructure folders, never used as working folders.
SKIP_DIRS = {"libs", "portable-git", "__pycache__"}

REQUIRED_PACKAGES = ["cryptography"]
OPTIONAL_PACKAGES = ["tkinterdnd2"]

GITHUB_LATEST_API = (
    "https://api.github.com/repos/git-for-windows/git/releases/latest"
)

# Fallback URLs, used when the latest-release lookup fails
# (for example offline or GitHub API rate limit).
WINDOWS_GIT_URLS = {
    "AMD64": (
        "https://github.com/git-for-windows/git/releases/download/"
        "v2.55.0.windows.1/PortableGit-2.55.0-64-bit.7z.exe"
    ),
    "ARM64": (
        "https://github.com/git-for-windows/git/releases/download/"
        "v2.55.0.windows.1/PortableGit-2.55.0-arm64.7z.exe"
    ),
}


# ------------------------------------------------------------
# Dependency bootstrap (portable, never touches OS Python)
# ------------------------------------------------------------

def install_packages(packages):
    """Install packages into ./libs beside this script."""
    LIBS_DIR.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade",
         "--no-compile", "--target", str(LIBS_DIR)] + list(packages),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    return result.returncode == 0, result.stdout


def bootstrap_dependencies():
    """
    Installs missing pip packages into ./libs beside this script.
    The operating system Python installation is never modified,
    which keeps the whole folder portable (SD card friendly).

    Required packages are fatal. Optional packages (drag & drop
    support) are best effort: failure only disables that feature.
    """
    if str(LIBS_DIR) not in sys.path:
        sys.path.insert(0, str(LIBS_DIR))

    def importable(package):
        try:
            __import__(package)
            return True
        except ImportError:
            return False

    missing = [p for p in REQUIRED_PACKAGES if not importable(p)]

    if missing:
        print(f"Installing required packages: {', '.join(missing)}")
        print(f"Install target: {LIBS_DIR}")

        try:
            ok, output = install_packages(missing)
        except Exception as error:
            print(f"Package installation failed: {error}")
            return False

        if not ok:
            print(output)
            return False

        if not all(importable(p) for p in missing):
            print("Import after installation failed.")
            return False

    optional = [p for p in OPTIONAL_PACKAGES if not importable(p)]

    if optional:
        print(f"Installing optional packages: {', '.join(optional)}")

        try:
            ok, output = install_packages(optional)

            if not ok:
                print(output)
                print("Optional packages unavailable, continuing.")
        except Exception as error:
            print(f"Optional install failed: {error} Continuing.")

    print("Dependencies ready.")
    return True


def load_tkdnd():
    """Return the tkinterdnd2 module, or None when unavailable."""
    try:
        import tkinterdnd2
        return tkinterdnd2
    except Exception:
        return None


def helper_interpreter():
    """
    Absolute interpreter path for the askpass helper scripts.
    Falls back from pythonw.exe to python.exe, because pythonw
    has no usable stdout and Git would read an empty answer.
    """
    executable = Path(sys.executable)

    if executable.name.lower().startswith("pythonw"):
        console_python = executable.with_name("python.exe")
        if console_python.exists():
            return str(console_python)

    return str(sys.executable)


# ------------------------------------------------------------
# Git detection and downloading
# ------------------------------------------------------------

def resolve_windows_git_url(log):
    """
    Resolve the PortableGit download URL. Tries the latest GitHub
    release first; falls back to the built-in URL because the
    unauthenticated GitHub API is rate limited.
    """
    architecture = platform.machine().upper()
    want_arm = architecture in ("ARM64", "AARCH64")

    try:
        request = urllib.request.Request(
            GITHUB_LATEST_API,
            headers={
                "User-Agent": "Portable-Git-Tkinter-App",
                "Accept": "application/vnd.github+json",
            }
        )

        with urllib.request.urlopen(request, timeout=10) as response:
            release = json.loads(response.read().decode("utf-8"))

        for asset in release.get("assets", []):
            name = asset.get("name", "")

            if not name.startswith("PortableGit-"):
                continue

            if not name.endswith(".7z.exe"):
                continue

            if want_arm and "arm64" in name:
                return asset["browser_download_url"]

            if not want_arm and "64-bit" in name:
                return asset["browser_download_url"]

        log(
            "Latest Git release has no matching PortableGit asset.",
            "warning"
        )

    except Exception as error:
        log(f"Could not query latest Git release: {error}", "warning")

    fallback = WINDOWS_GIT_URLS["ARM64" if want_arm else "AMD64"]
    log("Using built-in Git download URL.", "info")
    return fallback


def find_git():
    """
    Find portable Git beside the script or system Git.
    The self-extracting archive extracts directly into the target
    folder, but a one-level-deep search is included so nested
    extraction layouts also work.
    """
    if os.name == "nt":
        relative_paths = [
            "cmd/git.exe",
            "bin/git.exe",
            "mingw64/bin/git.exe",
            "usr/bin/git.exe",
        ]
    else:
        relative_paths = [
            "bin/git",
            "usr/bin/git",
        ]

    search_roots = []

    if PORTABLE_GIT_DIR.is_dir():
        search_roots.append(PORTABLE_GIT_DIR)

        try:
            search_roots.extend(
                sorted(
                    entry for entry in PORTABLE_GIT_DIR.iterdir()
                    if entry.is_dir()
                )
            )
        except OSError:
            pass

    for root in search_roots:
        for relative in relative_paths:
            candidate = root / relative

            if candidate.exists():
                return str(candidate)

    return shutil.which("git")


def download_windows_git(root, log):
    """Download and extract portable Git for Windows."""

    url = resolve_windows_git_url(log)

    PORTABLE_GIT_DIR.mkdir(parents=True, exist_ok=True)

    archive_path = APP_DIR / "PortableGit-download.exe"

    log("Downloading portable Git for Windows...", "info")
    log("The download will be stored on the selected drive.", "info")

    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Portable-Git-Tkinter-App"}
        )

        with urllib.request.urlopen(request, timeout=120) as response:
            total = response.headers.get("Content-Length")
            total = int(total) if total else None
            downloaded = 0

            with open(archive_path, "wb") as output:
                while True:
                    block = response.read(1024 * 1024)

                    if not block:
                        break

                    output.write(block)
                    downloaded += len(block)

                    if total:
                        percent = downloaded * 100 // total
                        log(f"Downloaded {percent}%", "info")

        log("Extracting portable Git...", "info")

        # Git for Windows portable .7z.exe files are self-extracting
        # and extract their contents directly into the -o folder.
        result = subprocess.run(
            [
                str(archive_path),
                f"-o{PORTABLE_GIT_DIR}",
                "-y"
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace"
        )

        if result.returncode != 0:
            log(result.stdout, "error")
            raise RuntimeError("Portable Git extraction failed.")

        try:
            archive_path.unlink()
        except OSError:
            pass

        git_path = find_git()

        if not git_path:
            raise RuntimeError(
                "Git was downloaded, but git.exe could not be found."
            )

        log(f"Portable Git ready: {git_path}", "success")
        return git_path

    except Exception as error:
        log(f"Git download failed: {error}", "error")

        messagebox.showerror(
            "Git download failed",
            str(error),
            parent=root
        )

        return None


def ensure_git(root, log):
    """Find Git or offer to download it."""

    git_path = find_git()

    if git_path:
        log(f"Using Git: {git_path}", "success")
        return git_path

    system = platform.system()

    if system == "Windows":
        download = messagebox.askyesno(
            "Git not found",
            "Git was not found.\n\n"
            "Download portable Git to this folder?",
            parent=root
        )

        if download:
            return download_windows_git(root, log)

        return None

    if system == "Darwin":
        messagebox.showinfo(
            "Git required",
            "Git is not installed.\n\n"
            "Install it with:\n\n"
            "xcode-select --install",
            parent=root
        )
        return None

    if system == "Linux":
        messagebox.showinfo(
            "Git required",
            "Git is not installed.\n\n"
            "Install it using your Linux package manager, for example:\n\n"
            "Debian/Ubuntu: sudo apt install git\n"
            "Fedora: sudo dnf install git\n"
            "Arch: sudo pacman -S git",
            parent=root
        )
        return None

    messagebox.showerror(
        "Unsupported system",
        f"Automatic Git setup is not supported on {system}.",
        parent=root
    )

    return None


# ------------------------------------------------------------
# URL validation
# ------------------------------------------------------------

GIT_URL_PATTERN = re.compile(r"^[^/\s:@]+@[^/\s:]+:.+$")


def looks_like_git_url(url):
    """Basic check: a known scheme or an scp-like user@host:path."""
    lowered = url.lower()

    if lowered.startswith((
        "http://", "https://", "ssh://", "git://",
        "ftp://", "ftps://", "file://"
    )):
        return True

    return bool(GIT_URL_PATTERN.match(url))


# ------------------------------------------------------------
# Git GUI
# ------------------------------------------------------------

class GitGUI:
    def __init__(self, root, tkdnd=None):
        self.root = root
        self.tkdnd = tkdnd

        self.root.title("Portable Git Assistant")
        self.root.geometry("900x680")
        self.root.minsize(700, 550)

        self.selected_folder = ""
        self.session_token = None

        self.build_interface()
        self.autodetect_start_folder()

    # --------------------------------------------------------
    # User interface
    # --------------------------------------------------------

    def build_interface(self):
        style = ttk.Style()
        style.theme_use("clam")

        main = ttk.Frame(self.root, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        title = tk.Label(
            main,
            text="Portable Git Assistant",
            font=("Arial", 20, "bold"),
            fg="#6c5ce7"
        )
        title.pack(pady=(0, 12))

        ttk.Label(main, text="Repository URL:").pack(anchor="w")

        self.url_entry = ttk.Entry(main)
        self.url_entry.pack(fill=tk.X, pady=(3, 10))

        ttk.Label(main, text="Working folder:").pack(anchor="w")

        folder_frame = ttk.Frame(main)
        folder_frame.pack(fill=tk.X, pady=(3, 10))

        self.folder_entry = ttk.Entry(folder_frame)
        self.folder_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Button(
            folder_frame,
            text="Choose Folder",
            command=self.choose_folder
        ).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(main, text="Commit message:").pack(anchor="w")

        self.commit_entry = ttk.Entry(main)
        self.commit_entry.insert(0, "Update files")
        self.commit_entry.pack(fill=tk.X, pady=(3, 10))

        # Git identity, used for commits only (not stored).
        identity_frame = ttk.Frame(main)
        identity_frame.pack(fill=tk.X, pady=(0, 12))

        name_box = ttk.Frame(identity_frame)
        name_box.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        ttk.Label(name_box, text="Git name:").pack(anchor="w")
        self.name_entry = ttk.Entry(name_box)
        self.name_entry.insert(0, GIT_NAME)
        self.name_entry.pack(fill=tk.X)

        email_box = ttk.Frame(identity_frame)
        email_box.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        ttk.Label(email_box, text="Git email:").pack(anchor="w")
        self.email_entry = ttk.Entry(email_box)
        self.email_entry.insert(0, GIT_EMAIL)
        self.email_entry.pack(fill=tk.X)

        button_frame = ttk.Frame(main)
        button_frame.pack(fill=tk.X, pady=(0, 6))

        tk.Button(
            button_frame,
            text="Clone",
            command=self.clone_repository,
            bg="#0984e3",
            fg="white",
            activebackground="#74b9ff",
            width=12
        ).pack(side=tk.LEFT, padx=3)

        tk.Button(
            button_frame,
            text="Fetch",
            command=self.fetch_repository,
            bg="#00cec9",
            fg="white",
            activebackground="#81ecec",
            width=12
        ).pack(side=tk.LEFT, padx=3)

        tk.Button(
            button_frame,
            text="Commit",
            command=self.commit_changes,
            bg="#e17055",
            fg="white",
            activebackground="#fab1a0",
            width=12
        ).pack(side=tk.LEFT, padx=3)

        tk.Button(
            button_frame,
            text="Push",
            command=self.push_changes,
            bg="#00b894",
            fg="white",
            activebackground="#55efc4",
            width=12
        ).pack(side=tk.LEFT, padx=3)

        tk.Button(
            button_frame,
            text="Apply Patch",
            command=self.browse_patch,
            bg="#d63031",
            fg="white",
            activebackground="#ff7675",
            width=12
        ).pack(side=tk.LEFT, padx=3)

        tk.Button(
            button_frame,
            text="Encrypt Token",
            command=self.encrypt_token,
            bg="#6c5ce7",
            fg="white",
            activebackground="#a29bfe",
            width=12
        ).pack(side=tk.LEFT, padx=3)

        branch_frame = ttk.Frame(main)
        branch_frame.pack(fill=tk.X, pady=(0, 12))

        tk.Button(
            branch_frame,
            text="Make HEAD main",
            command=self.make_head_main,
            bg="#e84393",
            fg="white",
            activebackground="#fd79a8",
            width=16
        ).pack(side=tk.LEFT, padx=3)

        ttk.Button(
            branch_frame,
            text="Reset Token",
            command=self.reset_token
        ).pack(side=tk.RIGHT, padx=3)

        ttk.Button(
            branch_frame,
            text="Clear Log",
            command=self.clear_log
        ).pack(side=tk.RIGHT, padx=3)

        ttk.Label(main, text="Log:").pack(anchor="w")

        self.log = ScrolledText(
            main,
            wrap=tk.WORD,
            height=20,
            bg="#1e1e1e",
            fg="#dfe6e9",
            insertbackground="white",
            font=("Consolas", 10)
        )
        self.log.pack(fill=tk.BOTH, expand=True)

        self.log.tag_config("info", foreground="#dfe6e9")
        self.log.tag_config("success", foreground="#55efc4")
        self.log.tag_config("error", foreground="#ff7675")
        self.log.tag_config("warning", foreground="#fdcb6e")
        self.log.tag_config("command", foreground="#ffeaa7")

        self.write_log("Ready.", "success")
        self.write_log(f"Application folder: {APP_DIR}", "info")

        if self.tkdnd is not None:
            self.register_drop_target(self.root)
            self.write_log(
                "Drag & drop enabled: drop .patch/.diff files "
                "anywhere in the window.",
                "info"
            )
        else:
            self.write_log(
                "Drag & drop not available. Use the Apply Patch button.",
                "warning"
            )

    def register_drop_target(self, widget):
        """Recursively register the whole window as a drop target."""
        try:
            widget.drop_target_register(self.tkdnd.DND_FILES)
            widget.dnd_bind("<<Drop>>", self.on_drop)
        except Exception:
            return

        for child in widget.winfo_children():
            self.register_drop_target(child)

    def write_log(self, text, level="info"):
        self.log.insert(tk.END, str(text) + "\n", level)
        self.log.see(tk.END)
        self.root.update_idletasks()

    def clear_log(self):
        self.log.delete("1.0", tk.END)

    # --------------------------------------------------------
    # Folder and URL autodetection
    # --------------------------------------------------------

    def url_file_for_folder(self, folder):
        """Companion URL file: pygit/folder1 -> pygit/folder1.url"""
        return APP_DIR / f"{Path(folder).name}.url"

    def read_companion_url(self, folder):
        """Read the repository address from the sibling .url file."""
        url_file = self.url_file_for_folder(folder)

        if not url_file.exists():
            return None

        try:
            for line in url_file.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
        except OSError:
            pass

        return None

    def write_url_file(self, folder, url):
        """Save the companion .url file next to the folder."""
        url_file = self.url_file_for_folder(folder)

        try:
            url_file.write_text(url + "\n", encoding="utf-8")
            self.write_log(
                f"Saved {url_file.name} for autodetection.", "info"
            )
        except OSError as error:
            self.write_log(
                f"Could not write .url file: {error}", "warning"
            )

    def autodetect_start_folder(self):
        """
        Working folder = 1st child folder of the script folder that
        has a <name>.url companion file. If none has one, the 1st
        child folder is used anyway.
        """
        try:
            subfolders = sorted(
                entry for entry in APP_DIR.iterdir()
                if entry.is_dir()
                and entry.name not in SKIP_DIRS
                and not entry.name.startswith(".")
            )
        except OSError:
            subfolders = []

        for folder in subfolders:
            if self.url_file_for_folder(folder).exists():
                self.set_folder(
                    str(folder),
                    f"Auto-detected working folder: {folder}"
                )
                return

        if subfolders:
            self.set_folder(
                str(subfolders[0]),
                "No folder with .url companion found. "
                f"Using first folder: {subfolders[0]}"
            )
            return

        self.write_log(
            "No child folder found next to the script.",
            "warning"
        )

    def set_folder(self, folder, log_message=None):
        """Select a folder and load its companion .url address."""
        self.selected_folder = folder
        self.folder_entry.delete(0, tk.END)
        self.folder_entry.insert(0, folder)

        if log_message:
            self.write_log(log_message, "info")

        url = self.read_companion_url(folder)

        if url:
            self.url_entry.delete(0, tk.END)
            self.url_entry.insert(0, url)
            self.write_log(
                f"Loaded address from {Path(folder).name}.url: {url}",
                "info"
            )

    def choose_folder(self):
        folder = filedialog.askdirectory(title="Choose working folder")

        if folder:
            self.set_folder(folder, f"Selected folder: {folder}")

    # --------------------------------------------------------
    # Patch application
    # --------------------------------------------------------

    def on_drop(self, event):
        """Handle files dropped anywhere in the window."""
        try:
            # splitlist handles paths with spaces: "{C:/a b/x.patch}"
            paths = self.root.tk.splitlist(event.data)
        except Exception:
            paths = [event.data]

        patch_paths = [
            p for p in paths
            if str(p).lower().endswith((".patch", ".diff"))
        ]

        if not patch_paths:
            self.write_log(
                f"Dropped file is not a .patch/.diff: {event.data}",
                "warning"
            )
            return

        for patch_path in patch_paths:
            self.apply_patch(patch_path)

    def browse_patch(self):
        patch_path = filedialog.askopenfilename(
            title="Choose patch file",
            filetypes=[
                ("Patch files", "*.patch *.diff"),
                ("All files", "*.*")
            ]
        )

        if patch_path:
            self.apply_patch(patch_path)

    def apply_patch(self, patch_path):
        folder = self.get_folder()

        if not folder:
            return

        patch_file = Path(patch_path)

        if not patch_file.is_file():
            messagebox.showerror(
                "Patch missing",
                f"File not found:\n{patch_file}"
            )
            return

        self.write_log(f"Applying patch: {patch_file.name}", "info")

        self.run_git(
            ["apply", str(patch_file)],
            cwd=folder
        )

    # --------------------------------------------------------
    # Password and token encryption
    # --------------------------------------------------------

    def ask_password(self, title, prompt):
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.geometry("430x160")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        result = {"password": None}

        ttk.Label(
            dialog,
            text=prompt,
            wraplength=390
        ).pack(padx=15, pady=(15, 8))

        password_entry = ttk.Entry(dialog, show="*")
        password_entry.pack(fill=tk.X, padx=15)
        password_entry.focus_set()

        buttons = ttk.Frame(dialog)
        buttons.pack(pady=12)

        def accept():
            password = password_entry.get()

            if not password:
                messagebox.showwarning(
                    "Password required",
                    "Enter a password.",
                    parent=dialog
                )
                return

            result["password"] = password
            dialog.destroy()

        def cancel():
            dialog.destroy()

        ttk.Button(
            buttons,
            text="OK",
            command=accept
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            buttons,
            text="Cancel",
            command=cancel
        ).pack(side=tk.LEFT, padx=5)

        dialog.bind("<Return>", lambda event: accept())
        dialog.bind("<Escape>", lambda event: cancel())

        self.root.wait_window(dialog)

        return result["password"]

    def derive_key(self, password, salt):
        key = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2 ** 14,
            r=8,
            p=1,
            dklen=32
        )

        return base64.urlsafe_b64encode(key)

    def encrypt_token(self):
        from cryptography.fernet import Fernet

        if not TOKEN_TEXT_FILE.exists():
            messagebox.showerror(
                "Token missing",
                f"Create this file first:\n{TOKEN_TEXT_FILE}"
            )
            return

        token = TOKEN_TEXT_FILE.read_text(
            encoding="utf-8"
        ).strip()

        if not token:
            messagebox.showerror(
                "Empty token",
                "t.txt is empty."
            )
            return

        if TOKEN_ENCRYPTED_FILE.exists():
            replace = messagebox.askyesno(
                "Replace t.bin?",
                "t.bin already exists. Replace it?"
            )

            if not replace:
                return

        password = self.ask_password(
            "Encrypt Token",
            "Create a password for t.bin:"
        )

        if password is None:
            return

        confirmation = self.ask_password(
            "Confirm Password",
            "Enter the password again:"
        )

        if confirmation is None:
            return

        if password != confirmation:
            messagebox.showerror(
                "Password mismatch",
                "The passwords do not match."
            )
            return

        try:
            salt = os.urandom(16)
            key = self.derive_key(password, salt)

            encrypted_token = Fernet(key).encrypt(
                token.encode("utf-8")
            )

            # Salt is not secret. It is needed to derive the key later.
            TOKEN_ENCRYPTED_FILE.write_bytes(
                salt + encrypted_token
            )

            self.write_log(
                f"Created encrypted token: {TOKEN_ENCRYPTED_FILE.name}",
                "success"
            )

            delete_original = messagebox.askyesno(
                "Delete t.txt?",
                "Encryption succeeded.\n\n"
                "Do you want to delete the original t.txt?"
            )

            if delete_original:
                TOKEN_TEXT_FILE.unlink()
                self.write_log(
                    "Deleted original t.txt.",
                    "success"
                )
            else:
                self.write_log(
                    "Kept original t.txt.",
                    "warning"
                )

            messagebox.showinfo(
                "Encryption complete",
                f"Encrypted token saved as:\n{TOKEN_ENCRYPTED_FILE}"
            )

        except Exception as error:
            self.write_log(
                f"Encryption failed: {error}",
                "error"
            )

            messagebox.showerror(
                "Encryption failed",
                str(error)
            )

    def get_token(self):
        """
        Loads the token once per application session.

        Priority:
        1. Cached memory token
        2. t.txt
        3. Encrypted t.bin
        """
        from cryptography.fernet import Fernet, InvalidToken

        if self.session_token:
            return self.session_token

        if TOKEN_TEXT_FILE.exists():
            token = TOKEN_TEXT_FILE.read_text(
                encoding="utf-8"
            ).strip()

            if token:
                self.session_token = token
                self.write_log(
                    "Loaded token from t.txt for this session.",
                    "warning"
                )
                return self.session_token

        if not TOKEN_ENCRYPTED_FILE.exists():
            messagebox.showerror(
                "Token missing",
                "No t.txt or t.bin file was found."
            )
            return None

        password = self.ask_password(
            "Unlock Token",
            "Enter the password for t.bin:"
        )

        if password is None:
            return None

        try:
            encrypted_file = TOKEN_ENCRYPTED_FILE.read_bytes()

            if len(encrypted_file) <= 16:
                raise ValueError("Invalid encrypted token file.")

            salt = encrypted_file[:16]
            encrypted_data = encrypted_file[16:]

            key = self.derive_key(password, salt)

            token = Fernet(key).decrypt(
                encrypted_data
            ).decode("utf-8").strip()

            if not token:
                raise ValueError("Decrypted token is empty.")

            # Cached only in the current Python process.
            self.session_token = token

            self.write_log(
                "Token unlocked for this session.",
                "success"
            )

            return self.session_token

        except InvalidToken:
            messagebox.showerror(
                "Unlock failed",
                "Incorrect password or damaged t.bin."
            )

            self.write_log(
                "Incorrect password or damaged t.bin.",
                "error"
            )

            return None

        except Exception as error:
            messagebox.showerror(
                "Unlock failed",
                str(error)
            )

            self.write_log(
                f"Could not unlock token: {error}",
                "error"
            )

            return None

    def reset_token(self):
        """Forget the session token (for example after t.bin changed)."""
        if self.session_token:
            self.session_token = None
            self.write_log(
                "Session token cleared. It will be asked for again "
                "on next use.",
                "info"
            )
        else:
            self.write_log("No session token loaded.", "info")

    # --------------------------------------------------------
    # In-memory Git authentication
    # --------------------------------------------------------

    def askpass_server(
        self,
        token,
        server_ready,
        server_info,
        stop_event
    ):
        """
        Provides Git credentials through a local temporary socket.

        The token is never put into:
        - Git URL
        - command-line arguments
        - environment variables
        - Git credential storage
        """

        server = socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM
        )

        server.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1
        )

        server.bind(("127.0.0.1", 0))
        server.listen(5)
        server.settimeout(1)

        server_info["port"] = server.getsockname()[1]
        server_info["challenge"] = secrets.token_urlsafe(32)

        server_ready.set()

        try:
            while not stop_event.is_set():
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    continue

                with connection:
                    connection.settimeout(10)
                    received = b""

                    while True:
                        block = connection.recv(4096)

                        if not block:
                            break

                        received += block

                    try:
                        request = json.loads(
                            received.decode("utf-8")
                        )

                        valid = (
                            request.get("challenge")
                            == server_info["challenge"]
                        )

                        if not valid:
                            response = ""
                        elif request.get("type") == "username":
                            response = "git"
                        elif request.get("type") == "password":
                            response = token
                        else:
                            response = ""

                    except Exception:
                        response = ""

                    connection.sendall(
                        response.encode("utf-8")
                    )

        finally:
            server.close()

    def create_askpass_helper(self):
        """
        Creates a helper file without the token.

        The helper only connects to the local authentication socket.
        It embeds the exact interpreter path, so it works even when
        Python is not on the system PATH (portable setups).
        """

        helper_dir = Path(
            tempfile.mkdtemp(prefix="git_gui_")
        )

        interpreter = helper_interpreter()

        if os.name == "nt":
            helper_path = helper_dir / "askpass.cmd"

            helper_path.write_text(
                "@echo off\n"
                f'"{interpreter}" "%~dp0askpass_client.py" %*\n',
                encoding="utf-8"
            )

        else:
            helper_path = helper_dir / "askpass.sh"

            helper_path.write_text(
                "#!/bin/sh\n"
                f'exec {shlex.quote(interpreter)} '
                '"$(dirname "$0")/askpass_client.py" "$@"\n',
                encoding="utf-8"
            )

            os.chmod(helper_path, 0o700)

        client_path = helper_dir / "askpass_client.py"

        client_path.write_text(
            r'''
import json
import os
import socket
import sys

port = int(os.environ["GIT_GUI_ASKPASS_PORT"])
challenge = os.environ["GIT_GUI_ASKPASS_CHALLENGE"]

prompt = " ".join(sys.argv[1:]).lower()

if "username" in prompt:
    request_type = "username"
else:
    request_type = "password"

request = {
    "challenge": challenge,
    "type": request_type
}

connection = socket.create_connection(
    ("127.0.0.1", port),
    timeout=30
)

connection.sendall(
    json.dumps(request).encode("utf-8")
)

connection.shutdown(socket.SHUT_WR)

response = b""

while True:
    block = connection.recv(4096)

    if not block:
        break

    response += block

connection.close()

print(response.decode("utf-8"), end="")
''',
            encoding="utf-8"
        )

        if os.name != "nt":
            os.chmod(client_path, 0o700)

        return helper_dir, helper_path

    # --------------------------------------------------------
    # Git commands
    # --------------------------------------------------------

    def token_is_configured(self):
        """Whether the app has a token it could use after an auth failure."""
        if self.session_token:
            return True

        try:
            if TOKEN_TEXT_FILE.is_file() and TOKEN_TEXT_FILE.read_text(
                encoding="utf-8"
            ).strip():
                return True
        except (OSError, UnicodeError):
            pass

        return TOKEN_ENCRYPTED_FILE.is_file()

    @staticmethod
    def git_requested_credentials(output):
        """Recognize common HTTPS authentication failures, not SSH key errors."""
        text = output.lower()

        if "permission denied (publickey" in text:
            return False

        markers = (
            "authentication failed",
            "failed to authenticate",
            "could not read username",
            "could not read password",
            "terminal prompts disabled",
            "invalid username or password",
            "not authorized",
            "access denied",
            "write access to repository not granted",
            "support for password authentication was removed",
            "requested url returned error: 401",
            "requested url returned error: 403",
            "repository not found",
        )
        return any(marker in text for marker in markers)

    def clone_target_for_retry(self, args, cwd):
        """Return an originally empty clone destination safe to reset."""
        if len(args) < 3 or args[0] != "clone":
            return None, False

        target = Path(args[-1])

        if not target.is_absolute() and cwd:
            target = Path(cwd) / target

        if target.is_symlink():
            return None, False

        if not target.exists():
            return target, False

        if not target.is_dir():
            return None, False

        try:
            if any(target.iterdir()):
                return None, False
        except OSError:
            return None, False

        return target, True

    def clear_failed_clone(self, target, restore_empty_directory):
        """Remove only artifacts from a clone started in an empty path."""
        if target is None or not target.exists() or target.is_symlink():
            return

        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()

            if restore_empty_directory:
                target.mkdir(parents=True, exist_ok=True)

            self.write_log(
                "Removed the incomplete unauthenticated clone; "
                "the folder is ready to retry.",
                "info"
            )
        except OSError as error:
            self.write_log(
                f"Could not clean the incomplete clone: {error}",
                "warning"
            )

    def _run_git_command(self, command, cwd, env):
        """Run Git once, returning (exit code, combined output)."""
        self.write_log(
            f"$ {' '.join(command)}",
            "command"
        )

        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace"
            )

            output = []

            for line in process.stdout:
                output.append(line)
                self.write_log(line.rstrip(), "info")

            return process.wait(), "".join(output)

        except FileNotFoundError:
            self.write_log(
                "Git executable was not found.",
                "error"
            )
            return None, ""

        except Exception as error:
            self.write_log(str(error), "error")
            return None, ""

    def run_git(self, args, cwd=None, use_token=False):
        git_path = find_git()

        if not git_path:
            git_path = ensure_git(
                self.root,
                self.write_log
            )

        if not git_path:
            self.write_log(
                "Git is not available. Command aborted.",
                "error"
            )
            return False

        # Try network operations anonymously first. Public repositories,
        # SSH keys, and local Git operations should not unlock t.bin.
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"

        command = [git_path]

        if use_token:
            # Keep this app's token out of configured OS credential helpers.
            # The empty value also makes the first, anonymous attempt prompt-free.
            command += ["-c", "credential.helper="]
            env.pop("GIT_ASKPASS", None)

        command += args
        clone_target, restore_clone_directory = (
            self.clone_target_for_retry(args, cwd) if use_token
            else (None, False)
        )

        result, output = self._run_git_command(command, cwd, env)

        if result is None:
            return False

        if result == 0:
            self.write_log("Command completed successfully.", "success")
            return True

        if not use_token or not self.git_requested_credentials(output):
            self.write_log(
                f"Command failed with exit code {result}.",
                "error"
            )
            return False

        # If clone failed before authentication, remove only a partial clone
        # created in a destination that was empty when this operation began.
        self.clear_failed_clone(clone_target, restore_clone_directory)

        if not self.token_is_configured():
            self.write_log(
                "The remote requested credentials, but no app token is "
                "configured. Public repositories and SSH keys work without "
                "t.txt/t.bin; for a private HTTPS repository, add a token "
                "before retrying.",
                "warning"
            )
            self.write_log(
                f"Command failed with exit code {result}.",
                "error"
            )
            return False

        self.write_log(
            "The remote requires credentials. Unlocking the configured "
            "token and retrying.",
            "warning"
        )

        token = self.get_token()

        if not token:
            self.write_log(
                "No token was unlocked; command was not retried.",
                "warning"
            )
            return False

        server_ready = threading.Event()
        stop_event = threading.Event()
        server_info = {}
        auth_thread = threading.Thread(
            target=self.askpass_server,
            args=(token, server_ready, server_info, stop_event),
            daemon=True
        )
        helper_dir = None

        try:
            auth_thread.start()

            if not server_ready.wait(timeout=5) or "port" not in server_info:
                self.write_log(
                    "Could not start authentication service.",
                    "error"
                )
                return False

            helper_dir, helper_path = self.create_askpass_helper()
            token_env = env.copy()
            token_env["GIT_ASKPASS"] = str(helper_path)

            # These contain only local socket details, never the token.
            token_env["GIT_GUI_ASKPASS_PORT"] = str(server_info["port"])
            token_env["GIT_GUI_ASKPASS_CHALLENGE"] = (
                server_info["challenge"]
            )

            retry_result, _ = self._run_git_command(
                command,
                cwd,
                token_env
            )

            if retry_result is None:
                return False

            if retry_result == 0:
                self.write_log("Command completed successfully.", "success")
                return True

            self.write_log(
                f"Command failed with exit code {retry_result}.",
                "error"
            )
            return False

        except Exception as error:
            self.write_log(
                f"Authenticated Git retry failed: {error}",
                "error"
            )
            return False

        finally:
            stop_event.set()

            if auth_thread.is_alive():
                auth_thread.join(timeout=3)

            if helper_dir and helper_dir.exists():
                shutil.rmtree(helper_dir, ignore_errors=True)

    def get_folder(self):
        folder = self.folder_entry.get().strip()

        if not folder:
            messagebox.showwarning(
                "Folder missing",
                "Choose a folder first."
            )
            return None

        if not os.path.isdir(folder):
            messagebox.showerror(
                "Invalid folder",
                "The selected folder does not exist."
            )
            return None

        return folder

    def working_tree_clean(self, folder):
        """True when git status --porcelain reports no changes."""
        git_path = find_git()

        if not git_path:
            return False

        result = subprocess.run(
            [git_path, "status", "--porcelain"],
            cwd=folder,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace"
        )

        return result.returncode == 0 and not result.stdout.strip()

    def git_run(self, args, cwd=None, log_errors=True):
        """
        Run a read-only git command.

        Returns (success, stdout). Prefer this over git_output
        when the difference between "failed" and "empty output"
        matters, for example rev-parse --abbrev-ref @{u} on a
        branch that has no upstream yet.
        """
        git_path = find_git()

        if not git_path:
            git_path = ensure_git(
                self.root,
                self.write_log
            )

        if not git_path:
            self.write_log(
                "Git is not available. Command aborted.",
                "error"
            )
            return False, ""

        try:
            result = subprocess.run(
                [git_path] + args,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
        except Exception as error:
            if log_errors:
                self.write_log(str(error), "error")

            return False, ""

        if result.returncode != 0 and log_errors:
            detail = (
                result.stderr.strip() or result.stdout.strip()
            )

            self.write_log(
                f"git {' '.join(args)} failed: {detail}",
                "error"
            )

        return result.returncode == 0, result.stdout

    def git_output(self, args, cwd=None):
        """
        Run a read-only git command and return its stdout.

        Stays silent on failure, for queries whose failure is a
        normal answer, such as "branch has no upstream".
        """
        return self.git_run(args, cwd=cwd, log_errors=False)[1]

    def require_repository(self):
        """Working folder when it is a Git repository, else None."""
        folder = self.get_folder()

        if not folder:
            return None

        if not os.path.isdir(os.path.join(folder, ".git")):
            messagebox.showerror(
                "Not a Git repository",
                "The selected folder is not a Git repository."
            )
            return None

        return folder

    def clone_repository(self):
        url = self.url_entry.get().strip()

        if not url:
            messagebox.showwarning(
                "Repository URL missing",
                "Enter a repository URL."
            )
            return

        if not looks_like_git_url(url):
            proceed = messagebox.askyesno(
                "Unusual URL",
                "This does not look like a Git URL:\n\n"
                f"{url}\n\n"
                "Try to use it anyway?",
                parent=self.root
            )

            if not proceed:
                return

        folder = self.folder_entry.get().strip()

        if not folder:
            messagebox.showwarning(
                "Folder missing",
                "Choose a folder. The repository will be cloned "
                "directly into it, without a subfolder."
            )
            return

        folder_path = Path(folder)

        if not folder_path.exists():
            create = messagebox.askyesno(
                "Folder does not exist",
                "The folder does not exist:\n\n"
                f"{folder}\n\n"
                "Create it and clone into it?"
            )

            if not create:
                return

            try:
                folder_path.mkdir(parents=True)
            except OSError as error:
                messagebox.showerror(
                    "Could not create folder",
                    str(error)
                )
                return

        if not folder_path.is_dir():
            messagebox.showerror(
                "Not a folder",
                f"This is not a folder:\n{folder}"
            )
            return

        if (folder_path / ".git").exists():
            messagebox.showinfo(
                "Already a repository",
                "The selected folder already contains a "
                "repository.\n\nUse Commit and Push, or choose "
                "another folder."
            )
            return

        if not any(folder_path.iterdir()):
            success = self.run_git(
                ["clone", url, folder],
                cwd=str(APP_DIR),
                use_token=True
            )

            if success:
                self.write_url_file(folder_path, url)
                self.set_folder(
                    folder,
                    f"Repository cloned into: {folder}"
                )
            return

        connect = messagebox.askyesno(
            "Folder is not empty",
            "Git cannot clone into a folder that already "
            "contains files.\n\n"
            "Connect the existing files to the repository?\n\n"
            "This runs: init, remote add, fetch, checkout.\n"
            "Your files are kept; repository files with the same "
            "name are not overwritten."
        )

        if not connect:
            return

        self.connect_folder_to_repository(folder, url)

    def connect_folder_to_repository(self, folder, url):
        """
        Connect a non-empty folder to a repository:
        git init, add remote, fetch, track the default branch.
        """
        self.write_log(
            "Connecting existing folder to repository...",
            "info"
        )

        if not self.run_git(["init"], cwd=folder):
            return

        # remote add fails when a previous attempt got halfway,
        # which made the whole step impossible to retry.
        # set-url keeps it repeatable.
        _, remotes = self.git_run(["remote"], cwd=folder)

        if "origin" in remotes.split():
            remote_command = [
                "remote", "set-url", "origin", url
            ]
        else:
            remote_command = [
                "remote", "add", "origin", url
            ]

        if not self.run_git(remote_command, cwd=folder):
            return

        if not self.run_git(
            ["fetch", "origin"],
            cwd=folder,
            use_token=True
        ):
            return

        self.run_git(
            ["remote", "set-head", "origin", "--auto"],
            cwd=folder,
            use_token=True
        )

        ok, remote_branches = self.git_run(
            ["branch", "-r"],
            cwd=folder
        )

        if not ok:
            self.write_log(
                "Could not list the remote branches.",
                "error"
            )
            return

        if not remote_branches.strip():
            self.write_log(
                "Repository has no branches yet (empty "
                "repository). Folder is connected; Commit and "
                "Push will create the first branch.",
                "warning"
            )
            return

        branch = None

        for line in remote_branches.splitlines():
            if "origin/HEAD" in line and "->" in line:
                branch = line.split("->")[-1].strip()
                branch = branch.split("/", 1)[-1]
                break

        if not branch:
            for candidate in ("main", "master"):
                if f"origin/{candidate}" in remote_branches:
                    branch = candidate
                    break

        if not branch:
            self.write_log(
                "Fetched, but the default branch was not "
                "detected. Checkout manually, for example:\n"
                "git checkout --track origin/main",
                "warning"
            )
            return

        if not self.run_git(
            ["checkout", "--track", f"origin/{branch}"],
            cwd=folder
        ):
            self.write_log(
                "Checkout stopped: local files with the same "
                "name as repository files were not "
                "overwritten. Move them away, or run "
                "'git checkout -f' to overwrite.",
                "warning"
            )
            return

        self.write_url_file(Path(folder), url)
        self.set_folder(folder)
        self.write_log(
            f"Connected. Tracking origin/{branch}.",
            "success"
        )

    def fetch_repository(self):
        folder = self.require_repository()

        if not folder:
            return

        # --prune drops remote tracking branches that no longer
        # exist on the server, so renamed or deleted branches do
        # not linger in the local branch list.
        self.run_git(
            ["fetch", "--prune", "origin"],
            cwd=folder,
            use_token=True
        )

    # --------------------------------------------------------
    # Make the checked out branch the main branch
    # --------------------------------------------------------

    def current_branch(self, folder):
        """Name of the checked out branch, None on a detached HEAD."""
        ok, branch = self.git_run(
            ["symbolic-ref", "--short", "HEAD"],
            cwd=folder,
            log_errors=False
        )

        if not ok:
            return None

        return branch.strip() or None

    def branch_exists(self, folder, name):
        """True when the local branch refs/heads/<name> exists."""
        ok, _ = self.git_run(
            [
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/heads/{name}",
            ],
            cwd=folder,
            log_errors=False
        )

        return ok

    def remote_branch_exists(self, folder, name):
        """True when the tracking branch origin/<name> exists."""
        ok, _ = self.git_run(
            [
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/remotes/origin/{name}",
            ],
            cwd=folder,
            log_errors=False
        )

        return ok

    def remote_exists(self, folder, name="origin"):
        ok, remotes = self.git_run(
            ["remote"],
            cwd=folder,
            log_errors=False
        )

        return ok and name in remotes.split()

    def pick_old_name(self, folder, has_origin, avoid=None):
        """
        Pick the name that keeps the previous main branch.

        Returns (name, overwrite); name is None when cancelled.
        "avoid" is the branch that is becoming main, which must
        keep its own name until the rename happens.
        """
        if OLD_BRANCH != avoid:
            taken_local = self.branch_exists(folder, OLD_BRANCH)
            taken_remote = has_origin and self.remote_branch_exists(
                folder, OLD_BRANCH
            )

            if not taken_local and not taken_remote:
                return OLD_BRANCH, False

            where = (
                "locally and on origin"
                if taken_local and taken_remote
                else "on origin"
                if taken_remote
                else "locally"
            )

            overwrite = messagebox.askyesno(
                f"'{OLD_BRANCH}' already exists",
                f"A branch named '{OLD_BRANCH}' already exists "
                f"{where}.\n\n"
                "Replace it with the previous main?\n\n"
                "Yes: overwrite it, its current commits are lost.\n"
                "No: keep it and use the next free name."
            )

            if overwrite:
                return OLD_BRANCH, True

        for index in range(2, 100):
            candidate = f"{OLD_BRANCH}-{index}"

            if candidate == avoid:
                continue

            if self.branch_exists(folder, candidate):
                continue

            if has_origin and self.remote_branch_exists(
                folder, candidate
            ):
                continue

            self.write_log(
                f"'{OLD_BRANCH}' is taken, using "
                f"'{candidate}' instead.",
                "warning"
            )
            return candidate, False

        messagebox.showerror(
            "No free name",
            f"Every name from '{OLD_BRANCH}' to "
            f"'{OLD_BRANCH}-99' is taken. Rename or delete one of "
            "them first."
        )

        return None, False

    def make_head_main(self):
        """
        Make the checked out branch the main branch and keep the
        previous main as "old".

        Typical use: work continued on a side branch and that branch
        should become the main line of development.

        Local:  main -> old, <checked out branch> -> main
        Origin: main is force pushed with a lease, old is pushed so
                that no commit of the previous main is lost.
        """
        folder = self.require_repository()

        if not folder:
            return

        branch = self.current_branch(folder)

        if not branch:
            messagebox.showerror(
                "Detached HEAD",
                "No branch is checked out (detached HEAD).\n\n"
                "Check out the branch that should become "
                f"'{MAIN_BRANCH}', then try again."
            )
            return

        if branch == MAIN_BRANCH:
            messagebox.showinfo(
                "Already main",
                f"The checked out branch is already '{MAIN_BRANCH}'."
            )
            return

        has_origin = self.remote_exists(folder)

        if has_origin:
            self.write_log("Fetching origin first...", "info")

            if not self.run_git(
                ["fetch", "--prune", "origin"],
                cwd=folder,
                use_token=True
            ):
                return

        local_main = self.branch_exists(folder, MAIN_BRANCH)
        remote_main = has_origin and self.remote_branch_exists(
            folder, MAIN_BRANCH
        )
        old_branch = None
        overwrite = False

        if local_main or remote_main:
            old_branch, overwrite = self.pick_old_name(
                folder, has_origin, avoid=branch
            )

            if not old_branch:
                return

        local_lines = []

        if old_branch:
            if local_main:
                local_lines.append(
                    f"  rename '{MAIN_BRANCH}' -> '{old_branch}'"
                )
            else:
                local_lines.append(
                    f"  create '{old_branch}' from "
                    f"origin/{MAIN_BRANCH}"
                )

        local_lines.append(
            f"  rename '{branch}' -> '{MAIN_BRANCH}'"
        )

        remote_lines = []

        if not has_origin:
            remote_lines.append(
                "  no 'origin' remote, local rename only"
            )
        else:
            if remote_main:
                remote_lines.append(
                    f"  push '{MAIN_BRANCH}' (force with lease)"
                )
            else:
                remote_lines.append(
                    f"  push -u origin '{MAIN_BRANCH}'"
                )

            if old_branch:
                if overwrite and self.remote_branch_exists(
                    folder, old_branch
                ):
                    remote_lines.append(
                        f"  push '{old_branch}' (force with lease)"
                    )
                else:
                    remote_lines.append(
                        f"  push -u origin '{old_branch}'"
                    )

        proceed = messagebox.askyesno(
            "Make HEAD main",
            f"'{branch}' becomes '{MAIN_BRANCH}':\n\n"
            "Local:\n"
            + "\n".join(local_lines)
            + "\n\nRemote (origin):\n"
            + "\n".join(remote_lines)
            + "\n\nThe remote main will point to this branch. "
            "Anyone else working\non it has to reset to the new "
            "main.\n\n"
            "Uncommitted changes are kept as they are.\n\n"
            "Continue?"
        )

        if not proceed:
            self.write_log("Cancelled, nothing was changed.", "warning")
            return

        # 1. Keep the previous main reachable as "old".
        old_taken_local = (
            bool(old_branch)
            and self.branch_exists(folder, old_branch)
        )

        if local_main:
            if not self.run_git(
                [
                    "branch",
                    "-M" if old_taken_local else "-m",
                    MAIN_BRANCH,
                    old_branch,
                ],
                cwd=folder
            ):
                return
        elif remote_main:
            create_old = ["branch"]

            if old_taken_local:
                create_old.append("-f")

            create_old += [old_branch, f"origin/{MAIN_BRANCH}"]

            if not self.run_git(create_old, cwd=folder):
                return

        # 2. The checked out branch becomes main.
        if not self.run_git(
            ["branch", "-m", branch, MAIN_BRANCH],
            cwd=folder
        ):
            return

        self.write_log(
            f"Renamed '{branch}' to '{MAIN_BRANCH}' locally.",
            "success"
        )

        # 3. Publish the result.
        if has_origin:
            main_remote_sha = ""

            if remote_main:
                _, main_remote_sha = self.git_run(
                    ["rev-parse", f"origin/{MAIN_BRANCH}"],
                    cwd=folder
                )

                main_remote_sha = main_remote_sha.strip()

            push_main = ["push", "-u"]

            if main_remote_sha:
                # Refuses to run when the server moved since the
                # fetch above, so a concurrent push is not lost.
                push_main.append(
                    f"--force-with-lease={MAIN_BRANCH}:"
                    f"{main_remote_sha}"
                )

            if not self.run_git(
                push_main + ["origin", MAIN_BRANCH],
                cwd=folder,
                use_token=True
            ):
                self.write_log(
                    "The remote main was not updated. The "
                    "branches are renamed locally; fix the "
                    "problem above and push "
                    f"'{MAIN_BRANCH}' again.",
                    "warning"
                )
                return

            if old_branch:
                push_old = ["push", "-u"]

                if self.remote_branch_exists(folder, old_branch):
                    ok, old_sha = self.git_run(
                        ["rev-parse", f"origin/{old_branch}"],
                        cwd=folder
                    )

                    old_sha = old_sha.strip()

                    if ok and old_sha:
                        push_old.append(
                            f"--force-with-lease={old_branch}:"
                            f"{old_sha}"
                        )

                self.run_git(
                    push_old + ["origin", old_branch],
                    cwd=folder,
                    use_token=True
                )
        else:
            # The rename carried the upstream of the old branch over
            # to main; without a remote it points at nothing useful.
            self.git_run(
                ["branch", "--unset-upstream", MAIN_BRANCH],
                cwd=folder,
                log_errors=False
            )

        message = f"'{branch}' is now '{MAIN_BRANCH}'."

        if old_branch:
            message += (
                f" The previous main is kept as '{old_branch}'."
            )

        self.write_log(message, "success")

        _, branches = self.git_run(
            ["branch", "-vv"],
            cwd=folder,
            log_errors=False
        )

        if branches.strip():
            self.write_log(
                "Branches now:\n" + branches.rstrip(),
                "info"
            )

    def commit_changes(self):
        folder = self.require_repository()

        if not folder:
            return

        commit_message = self.commit_entry.get().strip()

        if not commit_message:
            messagebox.showwarning(
                "Commit message missing",
                "Enter a commit message."
            )
            return

        name = self.name_entry.get().strip() or GIT_NAME
        email = self.email_entry.get().strip() or GIT_EMAIL

        if not name or not email:
            messagebox.showwarning(
                "Identity missing",
                "Enter a Git name and email."
            )
            return

        # Identity is passed per commit and never stored anywhere.
        identity = [
            "-c", f"user.name={name}",
            "-c", f"user.email={email}"
        ]

        if not self.run_git(
            ["add", "-A"],
            cwd=folder
        ):
            return

        if self.working_tree_clean(folder):
            self.write_log(
                "Nothing to commit. Working tree is clean.",
                "warning"
            )
            return

        self.run_git(
            identity + ["commit", "-m", commit_message],
            cwd=folder
        )

    def push_changes(self):
        folder = self.require_repository()

        if not folder:
            return

        # A folder that was connected (not cloned) has no upstream
        # on the first push; -u origin HEAD creates it.
        # The query fails on a branch without upstream, which is
        # the answer being asked for, so it stays out of the log.
        has_upstream, _ = self.git_run(
            ["rev-parse", "--abbrev-ref", "@{u}"],
            cwd=folder,
            log_errors=False
        )

        push_args = ["push"]

        if not has_upstream:
            push_args = ["push", "-u", "origin", "HEAD"]

        self.run_git(
            push_args,
            cwd=folder,
            use_token=True
        )


# ------------------------------------------------------------
# Start application
# ------------------------------------------------------------

if __name__ == "__main__":
    if not bootstrap_dependencies():
        error_root = tk.Tk()
        error_root.withdraw()
        messagebox.showerror(
            "Missing dependencies",
            "Could not install required packages automatically.\n\n"
            "Check your internet connection and that pip works, "
            "then run:\n\n"
            f"{sys.executable} -m pip install cryptography"
        )
        sys.exit(1)

    tkdnd = load_tkdnd()

    try:
        root = tkdnd.TkinterDnD.Tk() if tkdnd else tk.Tk()
    except Exception as error:
        print(f"Drag & drop unavailable: {error}")
        tkdnd = None
        root = tk.Tk()

    app = GitGUI(root, tkdnd)
    root.mainloop()