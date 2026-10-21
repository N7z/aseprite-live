-- Draws a 2-frame slime in 10 saves, one stage per call, like an agent would.
--   aseprite -b --script-param out=slime.aseprite --script-param step=1 --script slime.lua
-- Steps: 1 empty canvas, 2-6 frame 1 (outline, fill, shadow, highlight, eyes),
-- 7-9 frame 2, 10 timing.
local out = app.params.out
local step = tonumber(app.params.step)

local FRAMES = {
  { y = 5, rows = {
    "......oooo......",
    "....oohhggoo....",
    "...ohhggggggo...",
    "..ohggggggggso..",
    ".ogggeggggegsso.",
    ".ogggeggggegsso.",
    ".oggggggggggsso.",
    ".osggggggggssso.",
    "..osssssssssso..",
    "...oooooooooo...",
  } },
  { y = 6, rows = {
    "......oooo......",
    "...oohhggggoo...",
    "..ohhggggggggo..",
    ".ohgggggggggsso.",
    "oggggeggggeggsso",
    "oggggeggggeggsso",
    "ogggggggggggssso",
    "osssssssssssssso",
    ".oooooooooooooo.",
  } },
}
local COLORS = { o = 0x3a1d2b, g = 0x4abf5b, s = 0x3c8a3a, h = 0x8ae8a8, e = 0x26121a }
local PLAN = { [2] = { 1, "o" }, [3] = { 1, "g" }, [4] = { 1, "s" }, [5] = { 1, "h" },
               [6] = { 1, "e" }, [7] = { 2, "o" }, [8] = { 2, "g" }, [9] = { 2, "she" } }

local function rgb(v)
  return app.pixelColor.rgba((v >> 16) & 255, (v >> 8) & 255, v & 255, 255)
end

local spr
if step == 1 then
  spr = Sprite(16, 16)
  spr.layers[1].name = "slime"
  spr:newEmptyFrame()
  spr:newTag(1, 2).name = "idle"
else
  spr = app.open(out)
end

local plan = PLAN[step]
if plan then
  local f, chars = plan[1], plan[2]
  local layer = spr.layers[1]
  local cel = layer:cel(f) or spr:newCel(layer, f)
  -- redraw on a full-canvas image: saved cels come back trimmed and offset
  local img = Image(spr.spec)
  img:drawImage(cel.image, cel.position)
  local fr = FRAMES[f]
  for r, row in ipairs(fr.rows) do
    for x = 1, #row do
      local ch = row:sub(x, x)
      if chars:find(ch, 1, true) then img:drawPixel(x - 1, fr.y + r - 1, rgb(COLORS[ch])) end
    end
  end
  cel.image = img
  cel.position = Point(0, 0)
elseif step == 10 then
  spr.frames[1].duration = 0.5
  spr.frames[2].duration = 0.25
end
spr:saveAs(out)
