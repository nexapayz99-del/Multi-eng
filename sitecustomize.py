"""sitecustomize.py — patches pyrogram raw for py-tgcalls 3.x on pyrogram 2.0.106."""
import sys


def _install_patches():
    try:
        import pyrogram.raw.types as types_mod
        import pyrogram.raw.functions.phone as phone_mod
        import pyrogram.raw.functions as funcs_mod
        import pyrogram.raw.functions.phone.join_group_call as jgc_mod
        import pyrogram.raw.functions.phone.leave_group_call as lgc_mod
        from pyrogram.raw.core import TLObject
    except Exception as e:
        sys.stderr.write(f"sitecustomize: pyrogram import failed: {e}\n")
        return

    class _StubTLObject(TLObject):
        ID = 0
        def __init__(self, *a, **k):
            for i, v in enumerate(a):
                setattr(self, f"_arg{i}", v)
            for kk, vv in k.items():
                setattr(self, kk, vv)
        def write(self): return b""
        @classmethod
        def read(cls, *a, **k): return None

    missing_types = {
        "InputGroupCallInviteMessage":                 0x2b95d4e2,
        "InputGroupCallStream":                        0x05962a4b,
        "InputGroupCallSlug":                          0xfe06823f,
        "InputGroupCallInviteMessageByLink":           0x8e5e0e5c,
        "GroupCallParticipantVideo":                   0x67753ac8,
        "GroupCallParticipantVideoSource":             0xa04c05c5,
        "GroupCallStreamChannel":                      0x80eb48af,
        "InputGroupCall":                              0xd8aa840f,
        "InputGroupCallStreamFolder":                  0xa3510364,
        "GroupCall":                                   0xefb2b91b,
        "GroupCallParticipant":                        0xeba636fe,
        "MessageActionConferenceCall":                 0x2ffe2fd7,
        "PhoneCallDiscardReasonMigrateConferenceCall": 0x9fbbdb70,
        "UpdateGroupCallChainBlocks":                  0xcee9c4a1,
    }
    for name, tid in missing_types.items():
        if not hasattr(types_mod, name):
            cls = type(name, (_StubTLObject,), {"ID": tid})
            setattr(types_mod, name, cls)
            try: sys.modules[f"pyrogram.raw.types.{name}"] = cls
            except Exception: pass
        if hasattr(types_mod, "__all__") and name not in types_mod.__all__:
            try: types_mod.__all__ = list(types_mod.__all__) + [name]
            except Exception: pass

    missing_funcs = {
        "GetGroupCallChainBlocks":           0xeeee22cc,
        "CreateConferenceCall":              0x7d0444bb,
        "GetGroupCallStreamChannels":        0x1ab21940,
        "GetGroupCallStreamRtmpUrl":         0xc5c1c8f4,
        "SendGroupCallMessage":              0xee1f9b1f,
        "SendGroupCallEncryptedMessage":     0xf5f1e4b1,
        "InviteConferenceCallParticipant":   0x4b6d4b8a,
        "SendConferenceCallBroadcast":       0xc9b7fbd3,
        "DeleteConferenceCallParticipants":  0x8ca60525,
        "GetGroupCallJoinAs":                0xef7c213a,
    }
    for name, tid in missing_funcs.items():
        if not hasattr(phone_mod, name):
            cls = type(name, (_StubTLObject,), {"ID": tid})
            setattr(phone_mod, name, cls)
        if not hasattr(funcs_mod, name):
            setattr(funcs_mod, name, getattr(phone_mod, name))

    for mod, cls_name in ((jgc_mod, "JoinGroupCall"),
                          (lgc_mod, "LeaveGroupCall")):
        Real = getattr(mod, cls_name, None)
        if Real is None:
            continue
        class Wrapper(Real):
            def __init__(self, *args, **kwargs):
                kwargs.pop("block", None)
                kwargs.pop("public_key", None)
                kwargs.pop("_block", None)
                super().__init__(*args, **kwargs)
        Wrapper.__name__ = cls_name
        setattr(phone_mod, cls_name, Wrapper)
        setattr(funcs_mod, cls_name, Wrapper)
        setattr(mod, cls_name, Wrapper)

    sys.stderr.write(
        "sitecustomize: patched pyrogram raw - "
        f"{len(missing_types)} types, {len(missing_funcs)} funcs, "
        "wrapped JoinGroupCall/LeaveGroupCall\n"
    )


try:
    _install_patches()
except Exception as e:
    sys.stderr.write(f"sitecustomize: patch failed: {e}\n")
