import heapq
import json
import math
from dataclasses import dataclass


def wrap(a):
    return math.atan2(math.sin(a), math.cos(a))


@dataclass
class Pose:
    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0


class Odometry:
    """Cumulative signed wheel travel in meters; first sample establishes baseline."""
    def __init__(self, track_width=.18, yaw_sign=1, left_turn_scale=1.0, right_turn_scale=1.0):
        if track_width <= 0: raise ValueError('track_width must be measured')
        if yaw_sign not in (-1, 1): raise ValueError('yaw_sign must be -1 or 1')
        if left_turn_scale <= 0 or right_turn_scale <= 0: raise ValueError('turn scale must be positive')
        self.track_width=track_width
        self.yaw_sign=yaw_sign
        self.left_turn_scale=left_turn_scale
        self.right_turn_scale=right_turn_scale
        self.pose=Pose()
        self.last=None
        self.uncertainty=0.0

    def update(self,t,left,right):
        if not all(math.isfinite(v) for v in (t,left,right)): raise ValueError('nonfinite odometry')
        if self.last is not None:
            pt,pl,pr=self.last
            if t<=pt: raise ValueError('out-of-order telemetry')
            dl,dr=left-pl,right-pr
            if max(abs(dl),abs(dr))/(t-pt)>1: raise ValueError('wheel jump/reset or excessive speed')
            da=self.yaw_sign*(dr-dl)/self.track_width
            # Tracked vehicles slip substantially during an in-place turn.  Use
            # independently measured left/right factors only when the wheels
            # travel in opposite directions; ordinary driving keeps the
            # physical track-width model.
            if dl*dr < 0:
                da *= self.left_turn_scale if da > 0 else self.right_turn_scale
            distance=(dl+dr)/2
            if abs(da)<1e-8:
                self.pose.x+=distance*math.cos(self.pose.yaw)
                self.pose.y+=distance*math.sin(self.pose.yaw)
            else:
                radius=distance/da
                self.pose.x+=radius*(math.sin(self.pose.yaw+da)-math.sin(self.pose.yaw))
                self.pose.y-=radius*(math.cos(self.pose.yaw+da)-math.cos(self.pose.yaw))
            self.pose.yaw=wrap(self.pose.yaw+da)
            self.uncertainty+=abs(distance)*.05+abs(da)*.01
        self.last=(t,left,right)
        return self.pose


class Grid:
    def __init__(self,size=120,resolution=.1):
        self.size=size;self.resolution=resolution
        self.origin=(-size*resolution/2,-size*resolution/2)
        self.cells=[[0 for _ in range(size)] for _ in range(size)] # log odds; zero unknown

    def cell(self,x,y):
        return (math.floor((x-self.origin[0])/self.resolution),math.floor((y-self.origin[1])/self.resolution))

    def world(self,c):
        return (self.origin[0]+(c[0]+.5)*self.resolution,self.origin[1]+(c[1]+.5)*self.resolution)

    def inside(self,c):return 0<=c[0]<self.size and 0<=c[1]<self.size

    def integrate(self,pose,rays,max_range=4):
        # rays: [bearing_radians, measured_range_m, hit_bool], camera/sensor origin at base.
        for bearing,distance,hit in rays:
            if not all(math.isfinite(v) for v in (bearing,distance)) or not 0<distance<=max_range:
                continue
            angle=pose.yaw+bearing
            end=self.cell(pose.x+distance*math.cos(angle),pose.y+distance*math.sin(angle))
            seen=set()
            for step in range(math.ceil(distance/(self.resolution/2))+1):
                d=min(distance,step*self.resolution/2)
                c=self.cell(pose.x+d*math.cos(angle),pose.y+d*math.sin(angle))
                if c in seen or not self.inside(c):continue
                seen.add(c)
                delta=3 if hit and c==end else -1
                self.cells[c[1]][c[0]]=max(-10,min(10,self.cells[c[1]][c[0]]+delta))

    def blocked(self,radius=.25):
        # Unknown remains non-traversable. Inflate occupied cells by robot radius + clearance.
        result={(x,y) for y,row in enumerate(self.cells) for x,v in enumerate(row) if v>=0}
        n=math.ceil(radius/self.resolution)
        for y,row in enumerate(self.cells):
            for x,v in enumerate(row):
                if v<=0:continue
                for dy in range(-n,n+1):
                    for dx in range(-n,n+1):
                        if math.hypot(dx,dy)*self.resolution<=radius:result.add((x+dx,y+dy))
        return result

    def plan(self,start,goal,radius=.25):
        start=self.cell(*start);goal=self.cell(*goal);blocked=self.blocked(radius)
        # The robot already occupies its start cell. A newly inflated obstacle may
        # overlap that cell; allow planning an escape turn without treating the
        # current pose itself as a collision-free destination.
        blocked.discard(start)
        if not self.inside(start) or not self.inside(goal) or start in blocked or goal in blocked:return []
        pq=[(0,start)];cost={start:0};parent={start:None}
        while pq:
            _,cur=heapq.heappop(pq)
            if cur==goal:break
            for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
                nxt=(cur[0]+dx,cur[1]+dy)
                if not self.inside(nxt) or nxt in blocked:continue
                value=cost[cur]+1
                if value<cost.get(nxt,float('inf')):
                    cost[nxt]=value;parent[nxt]=cur
                    heapq.heappush(pq,(value+abs(nxt[0]-goal[0])+abs(nxt[1]-goal[1]),nxt))
        if goal not in parent:return []
        path=[];cur=goal
        while cur is not None:path.append(self.world(cur));cur=parent[cur]
        return list(reversed(path))

    def save(self,path):
        with open(path,'w',encoding='utf-8') as f:
            json.dump(dict(size=self.size,resolution=self.resolution,origin=self.origin,cells=self.cells),f)


class LocalScanMatcher:
    """Local occupancy correlation, not loop closure or globally consistent SLAM."""
    def match(self,grid,prior,rays):
        hits=[(a,d) for a,d,h in rays if h and 0<d<=4]
        if len(hits)<8:return prior,False
        def score(p):
            count=0
            for a,d in hits:
                c=grid.cell(p.x+d*math.cos(p.yaw+a),p.y+d*math.sin(p.yaw+a))
                if grid.inside(c) and grid.cells[c[1]][c[0]]>=3:count+=1
            return count/len(hits)
        baseline=score(prior);best=(baseline,prior)
        for dx in (-.1,0,.1):
            for dy in (-.1,0,.1):
                for da in (-.05,0,.05):
                    p=Pose(prior.x+dx,prior.y+dy,wrap(prior.yaw+da));s=score(p)
                    if s>best[0]:best=(s,p)
        return (best[1],True) if best[0]>=.7 and best[0]-baseline>=.2 else (prior,False)


class Patrol:
    def __init__(self,grid):
        self.grid=grid;self.points=[];self.index=0;self.state='idle';self.reason=''
        self.observe_until=None;self.path=[]

    def start(self,points):
        if not points or any(len(p)!=3 or not all(math.isfinite(v) for v in p) or p[2]<1 for p in points):
            raise ValueError('points require x_m, y_m, observation_seconds >=1')
        self.points=points;self.index=0;self.state='navigating';self.observe_until=None

    def pause(self):self.state='paused';self.reason='operator pause';self.observe_until=None
    def resume(self):
        if self.state not in ('paused','blocked','localization_lost'):raise ValueError('not resumable')
        self.state='navigating';self.reason='';self.observe_until=None

    def step(self,pose,now,sample_time,uncertainty,front_m,measured_speed):
        if self.state in ('idle','paused','complete'):return 'stop'
        if not all(math.isfinite(v) for v in (now,sample_time,uncertainty,front_m,measured_speed)) or not 0<=now-sample_time<=.5 or uncertainty>.4:
            self.state='localization_lost';self.reason='stale or uncertain telemetry';return 'stop'
        if self.state in ('blocked','localization_lost'):return 'stop'
        if front_m<.35:
            self.state='blocked';self.reason='near obstacle';return 'stop'
        x,y,hold=self.points[self.index]
        if math.hypot(x-pose.x,y-pose.y)<.15:
            self.state='observing'
            if abs(measured_speed)>.02:self.observe_until=None;return 'stop'
            if self.observe_until is None:self.observe_until=now+hold
            if now>=self.observe_until:
                self.index+=1;self.observe_until=None
                self.state='complete' if self.index==len(self.points) else 'navigating'
            return 'stop'
        self.state='navigating';self.observe_until=None
        self.path=self.grid.plan((pose.x,pose.y),(x,y))
        if len(self.path)<2:self.state='blocked';self.reason='no known free route';return 'stop'
        tx,ty=self.path[min(2,len(self.path)-1)]
        angle=wrap(math.atan2(ty-pose.y,tx-pose.x)-pose.yaw)
        if abs(angle)>.2:return 'left' if angle>0 else 'right'
        return 'forward'


def depth_advisory(depth):
    """Only relative image-space summary. NEVER supplies metric rays to Grid."""
    import numpy as np
    a=np.asarray(depth,dtype=float)
    if a.ndim!=2 or a.shape[1]<3 or not np.isfinite(a).all():raise ValueError('invalid depth map')
    return dict(units='relative_not_meters',usable_for_localization=False,
                region_medians=[float(np.median(p)) for p in np.array_split(a,3,axis=1)])
