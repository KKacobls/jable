# author: hcjohn463 (original upstream project: https://github.com/hcjohn463/JableTVDownload)
# Modified in KKacobls/jable (Apache-2.0): replaced original CLI entry point with a Flask server launcher.
"""Compatibility launcher for password-free JSON API + static frontend."""
import runpy

if __name__ == '__main__':
    runpy.run_module('app',run_name='__main__')
