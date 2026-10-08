import unittest

from laser import dbus


class MarshalTest(unittest.TestCase):
    def roundtrip(self, sig, args):
        buf = bytearray()
        for t, v in zip(dbus._split(sig), args):
            dbus._put(buf, t, v)
        out, pos = [], 0
        for t in dbus._split(sig):
            v, pos = dbus._get(bytes(buf), pos, t)
            out.append(v)
        return out

    def test_split(self):
        self.assertEqual(dbus._split("susssasa{sv}i"), ["s", "u", "s", "s", "s", "as", "a{sv}", "i"])
        self.assertEqual(dbus._split("a(yv)"), ["a(yv)"])

    def test_notify_arguments_roundtrip(self):
        args = ("Laser", 7, "", "You drifted to youtube.com", "Back to: thesis ✓", ["default", "It's for my task"],
                {"urgency": ("y", 2)}, -1)
        out = self.roundtrip("susssasa{sv}i", args)
        self.assertEqual(out[:6], ["Laser", 7, "", "You drifted to youtube.com", "Back to: thesis ✓",
                                   ["default", "It's for my task"]])
        self.assertEqual(out[6], [("urgency", 2)])
        self.assertEqual(out[7], -1)

    def test_message_header_is_aligned(self):
        msg = dbus._message(1, 1, {"path": "/a", "interface": "b.c", "member": "M", "destination": "d.e"}, "s", ("x",))
        self.assertEqual(msg[0:1], b"l")
        fields, pos = dbus._get(msg, 12, "a(yv)")
        self.assertEqual(dict(fields)[3], "M")
        self.assertEqual((pos + (-pos % 8)) % 8, 0)


if __name__ == "__main__":
    unittest.main()
