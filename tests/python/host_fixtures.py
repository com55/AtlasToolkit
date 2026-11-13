# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Small synthetic, position-coded pages for the app host tests."""
from io import BytesIO

from PIL import Image


def png(image):
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def decode(data):
    return Image.open(BytesIO(data)).convert("RGBA")


def pixel(x, y):
    return (10 + x * 4, 20 + y * 4, 30 + x + y, 128)


def page_bytes(width=9, height=7):
    image = Image.new("RGBA", (width, height))
    image.putdata([pixel(x, y) for y in range(height) for x in range(width)])
    return png(image)


def one_page(page="p.png", first="a", pma=False):
    text = (
        f"{page}\nsize: 9,7\npma: {str(pma).lower()}\n"
        f"{first}\n bounds: 1,2,3,2\nb\n bounds: 5,1,2,3\n"
    )
    return text, {page: page_bytes()}


def two_pages(first_pma=False, second_pma=False):
    text, pages = one_page(pma=first_pma)
    text += f"\nq.png\nsize: 8,5\npma: {str(second_pma).lower()}\nc\n bounds: 2,1,3,2\n"
    pages["q.png"] = page_bytes(8, 5)
    return text, pages


def all_pma():
    return two_pages(True, True)


def mixed_pma():
    return two_pages(True, False)
