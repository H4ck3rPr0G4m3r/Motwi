"""What Motwi.exe runs. The installed app has no console, so only the app's own entry point
ships; the `motwi` command line stays a development tool."""

from motwi.desktop import main

if __name__ == "__main__":
    main()
