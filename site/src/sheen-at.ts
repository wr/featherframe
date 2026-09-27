/** Where the glass's highlight crosses it (0 top-left … 1 bottom-right) for a
 *  frame whose middle is `y` px down a viewport `vh` tall: a light up in the
 *  room, its reflection sliding down the glass as the frame rises. Shared by
 *  the 3D frame (viewer.ts) and the wall's HTML frames (sheen.ts). */
export const sheenAt = (y: number, vh: number) => 0.18 + 0.64 * Math.max(0, Math.min(1, y / vh));
