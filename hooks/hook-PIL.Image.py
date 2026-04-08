from PyInstaller.utils.hooks import collect_submodules

# The app only advertises support for the formats below in its OCR/image picker.
# Keeping the plugin list narrow avoids bundling large optional codecs like AVIF.
hiddenimports = []
for plugin in (
    "PIL.BmpImagePlugin",
    "PIL.GifImagePlugin",
    "PIL.IcoImagePlugin",
    "PIL.JpegImagePlugin",
    "PIL.PngImagePlugin",
    "PIL.TiffImagePlugin",
):
    hiddenimports += collect_submodules(plugin)
