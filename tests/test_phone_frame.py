from pathlib import Path

import pytest

from edit_plan import PhonePage
from phone_frame import PhoneFrameError, draw, layouts

PHONE = {"width": 390, "height": 844, "background": "#3050a0"}
TAKE = (320, 240)


def page(width=80, height=240, window=(100, 240), dpr=1):
    return PhonePage(window, (width, height), dpr)


def lay_out(pages, viewport=(320, 240), clear_below=40, take=TAKE):
    return layouts(pages, take, PHONE, viewport, clear_below)


def test_the_page_is_cropped_from_the_top_left_of_the_centred_window():
    assert lay_out({"pick": page()})["pick"].crop == (110, 0, 80, 240)


def test_a_page_taller_than_the_take_is_cropped_to_it():
    assert lay_out({"pick": page(height=300)})["pick"].crop == (110, 0, 80, 240)


def test_the_screen_filters_crop_the_page_and_scale_it_to_the_screen():
    layout = lay_out({"pick": page()})["pick"]
    screen = f"{layout.geo.screen_width}:{layout.geo.screen_height}"
    assert layout.screen_filters() == ["crop=80:240:110:0", f"scale={screen}"]


@pytest.mark.parametrize(
    "pages",
    [
        {"pick": page(320, 240, window=(320, 240))},  # a desktop page
        # Chrome won't size a window below ~500 px wide: un-emulated, the page stays
        # 500x836, a little wider than the 390x844 phone yet nowhere near a desktop page
        {"pick": page(500, 836, window=(500, 836), dpr=0.25)},
    ],
)
def test_a_page_that_is_not_phone_shaped_is_refused_with_a_hint(pages):
    with pytest.raises(PhoneFrameError, match="phone scene 'pick'.*emulat"):
        lay_out(pages)


def test_subtitles_too_deep_to_leave_room_for_the_phone_are_refused_with_a_hint():
    with pytest.raises(PhoneFrameError, match="phone scene 'pick'.*shorter sentences"):
        lay_out({"pick": page()}, viewport=(180, 320), clear_below=250)


def test_each_page_shape_gets_its_own_frame_at_the_same_height():
    found = lay_out({"narrow": page(80), "wide": page(120, window=(120, 240))})
    narrow, wide = found["narrow"].geo, found["wide"].geo
    assert narrow.screen_height == wide.screen_height
    assert wide.screen_width / narrow.screen_width == pytest.approx(120 / 80, rel=0.05)


def test_a_vertical_video_fills_most_of_its_height_with_the_phone():
    geo = lay_out({"pick": page()}, viewport=(180, 320))["pick"].geo
    assert geo.height > 320 * 0.7


@pytest.mark.parametrize("clear_below", [0, 40, 90])
def test_the_frame_ends_above_the_subtitles(clear_below):
    geo = lay_out({"pick": page()}, clear_below=clear_below)["pick"].geo
    assert geo.y + geo.height <= 240 - clear_below


def test_scenes_of_one_page_shape_draw_the_mask_and_body_once(tmp_path):
    found = lay_out({"pick": page(), "post": page()})
    commands = []

    def run(cmd):
        commands.append(cmd)
        Path(cmd[-1]).touch()

    for index, layout in enumerate(found.values()):
        out = tmp_path / f"{index}.mp4"
        draw(tmp_path / "screen.mp4", layout, 90, 30, [], out, tmp_path, run)
    pngs = [cmd[-1] for cmd in commands if cmd[-1].endswith(".png")]
    assert len(pngs) == 2 and len(set(pngs)) == 2
    assert [cmd[-1] for cmd in commands if cmd[-1].endswith(".mp4")] == [
        str(tmp_path / "0.mp4"),
        str(tmp_path / "1.mp4"),
    ]
