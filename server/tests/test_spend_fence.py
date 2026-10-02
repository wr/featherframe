"""A paid model method runs only inside a purchase (W-938): a call site that
forgets the gate fails here, never in an owner's account."""
from __future__ import annotations

import pytest

from featherframe import spend
from featherframe.render import genart

PAID = [
    (genart.OpenAIImageModel, ("k",), "generate", ("p", "1024x1024", [])),
    (genart.GeminiImageModel, ("k",), "generate", ("p", "1024x1024", [])),
    (genart.ReplicateImageModel, ("k",), "generate", ("p", "1024x1024", [])),
    (genart.A1111ImageModel, ("http://gpu:7860",), "generate", ("p", "1024x1024", [])),
    (genart.OpenAITextModel, ("k",), "complete_json", ("brief",)),
    (genart.OpenAITextModel, ("k",), "search_json", ("weather",)),
    (genart.GeminiTextModel, ("k",), "complete_json", ("brief",)),
    (genart.AnthropicTextModel, ("k",), "complete_json", ("brief",)),
    (genart.LocalTextModel, ("http://gpu:11434",), "complete_json", ("brief",)),
]


@pytest.mark.parametrize("cls, args, method, call", PAID,
                         ids=[f"{c.__name__}.{m}" for c, _, m, _ in PAID])
def test_every_paid_method_is_fenced(cls, args, method, call, monkeypatch):
    def no_network(*a, **kw):
        raise AssertionError("reached the network outside a purchase")
    monkeypatch.setattr(genart.requests, "post", no_network)
    with pytest.raises(spend.Unguarded):
        getattr(cls(*args), method)(*call)


def test_a_new_model_class_is_fenced_too():
    class Later(genart.ImageModel):
        def generate(self, prompt, size, refs):
            return b"png"
    with pytest.raises(spend.Unguarded):
        Later().generate("p", "1x1", [])
    with spend.Gate.unlimited().purchase("plate", "x", model="later"):
        assert Later().generate("p", "1x1", []) == b"png"
