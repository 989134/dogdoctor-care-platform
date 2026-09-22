# planner.py
import heapq
import math

def to_cell(x, y, cell):
    return int(x // cell), int(y // cell)

def to_world(cx, cy, cell):
    return (cx + 0.5) * cell, (cy + 0.5) * cell

def neighbors(c):
    x, y = c
    return [(x+1,y),(x-1,y),(x,y+1),(x,y-1),
            (x+1,y+1),(x-1,y-1),(x+1,y-1),(x-1,y+1)]

def astar(occ_grid, start_xy, goal_xy, cell):
    """
    occ_grid: 2D list/array, 0=free, 1=occupied
    start_xy, goal_xy: world coordinate
    return: path as list of world points [(x,y),...]
    """
    W = len(occ_grid[0])
    H = len(occ_grid)
    start = to_cell(*start_xy, cell)
    goal  = to_cell(*goal_xy, cell)

    def inb(n):
        return 0 <= n[0] < W and 0 <= n[1] < H

    def free(n):
        return occ_grid[n[1]][n[0]] == 0

    if not inb(start) or not inb(goal) or (not free(start)) or (not free(goal)):
        return []

    def h(a, b):
        return math.hypot(a[0]-b[0], a[1]-b[1])

    pq = [(0.0, start)]
    came = {start: None}
    g = {start: 0.0}

    while pq:
        _, cur = heapq.heappop(pq)
        if cur == goal:
            break
        for nb in neighbors(cur):
            if not inb(nb) or not free(nb):
                continue
            step = h(cur, nb)
            ng = g[cur] + step
            if nb not in g or ng < g[nb]:
                g[nb] = ng
                f = ng + h(nb, goal)
                heapq.heappush(pq, (f, nb))
                came[nb] = cur

    if goal not in came:
        return []

    # reconstruct
    path_cells = []
    cur = goal
    while cur is not None:
        path_cells.append(cur)
        cur = came[cur]
    path_cells.reverse()
    return [to_world(cx, cy, cell) for (cx, cy) in path_cells]