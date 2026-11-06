"""
setup.py  -  Cython compilation for the HFT engine
===================================================
Compiles cython_src/lob.pyx with aggressive C compiler optimisations.

Requirements:
    pip install cython
    + MSVC (Visual Studio Build Tools) on Windows
    + GCC / Clang on Linux/macOS

Usage:
    python setup.py build_ext --inplace

After compilation:
    from cython_src.lob import CythonLOB
"""

import sys

from setuptools import Extension, setup

try:
    from Cython.Build import cythonize
    HAS_CYTHON = True
except ImportError:
    HAS_CYTHON = False
    print("WARNING: Cython not installed.  pip install cython")

if HAS_CYTHON:
    # Platform-specific compiler flags
    if sys.platform == "win32":
        extra_compile_args = ["/O2", "/fp:fast"]
        extra_link_args    = []
    else:
        extra_compile_args = ["-O3", "-march=native", "-ffast-math"]
        extra_link_args    = []

    ext = Extension(
        name="cython_src.lob",
        sources=["cython_src/lob.pyx"],
        extra_compile_args=extra_compile_args,
        extra_link_args=extra_link_args,
    )
    setup(
        name="hft_engine",
        ext_modules=cythonize(
            [ext],
            compiler_directives={
                "language_level"   : "3",
                "boundscheck"      : False,
                "wraparound"       : False,
                "cdivision"        : True,
                "nonecheck"        : False,
                "initializedcheck" : False,
            },
            annotate=True,
        ),
        zip_safe=False,
    )
else:
    setup(name="hft_engine")
