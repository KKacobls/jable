"""Compatibility launcher for password-free JSON API + static frontend."""
import runpy

if __name__ == '__main__':
    runpy.run_module('app',run_name='__main__')
