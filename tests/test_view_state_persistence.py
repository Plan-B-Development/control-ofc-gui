class TestGeometryReachability:
    """DEC-257: a saved geometry must still land on a connected screen.

    `_as_geometry` sanity-bounds the stored values but never checked them against
    the *current* display layout. Save the window on a second monitor, unplug it,
    reopen — and the window is restored where no screen exists, unreachable
    except by editing the settings file. Qt's own `restoreGeometry()` does this
    check; the raw `setGeometry()` this uses does not.
    """

    def test_a_geometry_on_a_connected_screen_is_accepted(self, qtbot):
        from PySide6.QtGui import QGuiApplication

        from control_ofc.ui.main_window import MainWindow

        avail = QGuiApplication.primaryScreen().availableGeometry()
        on_screen = [avail.x() + 20, avail.y() + 20, 800, 600]
        assert MainWindow._geometry_is_reachable(on_screen)

    def test_a_geometry_on_a_vanished_monitor_is_rejected(self, qtbot):
        """The unplugged-second-monitor case."""
        from control_ofc.ui.main_window import MainWindow

        assert not MainWindow._geometry_is_reachable([25000, 25000, 800, 600])

    def test_a_barely_overlapping_window_is_rejected(self, qtbot):
        """One pixel on-screen is not a usable window — require enough to grab."""
        from PySide6.QtGui import QGuiApplication

        from control_ofc.ui.main_window import MainWindow

        avail = QGuiApplication.primaryScreen().availableGeometry()
        sliver = [avail.right() - 2, avail.bottom() - 2, 800, 600]
        assert not MainWindow._geometry_is_reachable(sliver)
