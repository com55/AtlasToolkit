"""JSON calls with binary payloads carried in a separate buffer list."""
import json


def call(host, method: str, args_json: str, buffers: list[bytes]) -> tuple[str, list[bytes]]:
    """Invoke a public callable and return a JSON envelope plus output buffers."""
    output = []

    def decode(value):
        if isinstance(value, list):
            return [decode(item) for item in value]
        if isinstance(value, dict):
            if set(value) == {"$buf"}:
                return buffers[value["$buf"]]
            return {key: decode(item) for key, item in value.items()}
        return value

    def encode(value):
        if isinstance(value, (bytes, bytearray, memoryview)):
            index = len(output)
            output.append(bytes(value))
            return {"$buf": index}
        if isinstance(value, (list, tuple)):
            return [encode(item) for item in value]
        if isinstance(value, dict):
            return {key: encode(item) for key, item in value.items()}
        return value

    try:
        if method.startswith("_"):
            raise ValueError(f"method {method!r} is not a public callable")
        target = getattr(host, method, None)
        if not callable(target):
            raise ValueError(f"method {method!r} is not a public callable")
        args = json.loads(args_json)
        if not isinstance(args, list):
            raise ValueError("args_json must be a JSON array")
        value = encode(target(*decode(args)))
        text = json.dumps({"ok": True, "value": value}, ensure_ascii=False, separators=(",", ":"))
        return text, output
    except Exception as error:
        return json.dumps(
            {"ok": False, "error": {"type": type(error).__name__, "message": str(error)}},
            ensure_ascii=False, separators=(",", ":"),
        ), []
