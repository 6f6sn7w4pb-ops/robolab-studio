import os
import sys

# OpenCV's Qt5 plugin path must never reach the independent Qt6 GUI process.
for key in ('QT_QPA_PLATFORM_PLUGIN_PATH', 'QT_QPA_FONTDIR'):
    os.environ.pop(key,None)

def main():
    from .gui import configure_application, MainWindow
    app=configure_application()
    window=MainWindow(); window.show()
    return app.exec()

if __name__=='__main__': sys.exit(main())
