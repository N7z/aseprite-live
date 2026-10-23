import unittest

from aseprite_live.app import App
from aseprite_live.server import make_server


class ServerTest(unittest.TestCase):
    def test_busy_port_moves_on(self):
        # A second viewer must not share a port with a running one (on Windows
        # SO_REUSEADDR would let it), it has to take the next free port.
        first = make_server(App([]), 0)
        try:
            port = first.server_address[1]
            second = make_server(App([]), port)
            try:
                self.assertNotEqual(second.server_address[1], port)
            finally:
                second.server_close()
        finally:
            first.server_close()


if __name__ == "__main__":
    unittest.main()
