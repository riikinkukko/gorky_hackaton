"""Регрессия со стенда: веса GigaAM v3 во float16 → ошибка Half vs float на CPU."""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from app.voice.gigaam_loader import to_float32  # noqa: E402


class Tiny(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.conv = torch.nn.Conv1d(1, 4, 3)
        self.norm = torch.nn.BatchNorm1d(4)
        self.encoder = torch.nn.Sequential(torch.nn.Linear(4, 4))

    def forward(self, x):  # type: ignore[no-untyped-def]
        h = self.norm(self.conv(x)).mean(-1)
        return self.encoder(h)


def _half_model() -> Tiny:
    m = Tiny().eval()
    return m.half()


def test_half_weights_fail_without_fix() -> None:
    with pytest.raises(RuntimeError):
        _half_model()(torch.randn(1, 1, 16))


def test_to_float32_fixes_fp16_checkpoint() -> None:
    out = to_float32(_half_model())(torch.randn(1, 1, 16))
    assert out.dtype == torch.float32


def test_to_float32_keeps_int8_dynamic_linear() -> None:
    m = _half_model()
    m.encoder = torch.ao.quantization.quantize_dynamic(
        m.encoder.float(), {torch.nn.Linear}, dtype=torch.qint8
    )
    m = to_float32(m)
    assert "quantized" in type(m.encoder[0]).__module__
    assert m(torch.randn(1, 1, 16)).dtype == torch.float32
