"""The PKG RLC Extractor package.

One SUBPACKAGE PER LAYER, in the order tests/test_layering.py
declares.  A module may import from its own layer or a lower one;
upward is the failure, and now it is also visible in the tree:

    physics/   L0  arrays and physics
    model/     L1  the shared data model and the spec logic on it
    services/  L2  the session file, a run
    present/   L3  turning a result into text
    widgets/   L4  generic Tk widgets
    panels/    L5  app-specific windows and panels
    frontend/  L6  the App and the argv entry point

Every __init__.py in here is EMPTY of imports on purpose.  A
package that imported its own modules would make
`import pkg_rlc.physics.core` drag in tkinter, and would put an
edge in the import graph that the layering gate cannot see.
"""
