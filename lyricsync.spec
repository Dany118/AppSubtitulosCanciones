# PyInstaller build recipe.  Build with:  pyinstaller lyricsync.spec
#
# Produces a single self-contained executable for the interface and the
# lyrics-database path -- no Python installation needed on the target machine.
#
# The heavy ML stack is deliberately EXCLUDED. Bundling torch, CUDA and the
# Demucs/Whisper models would turn a ~25 MB download into several gigabytes,
# and a frozen build has no pip to fetch models at runtime. Forced alignment
# and transcription therefore need the normal install (install.bat / install.sh).

block_cipher = None

analysis = Analysis(
    ["src/lyricsync/__main__.py"],
    pathex=["src"],
    binaries=[],
    # The web interface is served from these files; without this the packaged
    # app starts and then 404s on its own page.
    datas=[("src/lyricsync/gui/static", "lyricsync/gui/static")],
    hiddenimports=["lyricsync.gui", "lyricsync.gui.server", "lyricsync.providers"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "torch", "torchaudio", "demucs", "faster_whisper", "ctranslate2",
        "matplotlib", "scipy", "pandas", "PIL", "tkinter.test", "pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(analysis.pure, analysis.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.zipfiles,
    analysis.datas,
    [],
    name="lyricsync",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # Keep the console: it shows the local address and any error, and closing
    # the window is how the user stops the server.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
