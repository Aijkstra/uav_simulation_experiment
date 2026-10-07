import { useEffect, useRef, useState } from 'react'
import type { Frame, Route, TrialDetail, WindZone } from './types'

export const SIMULATION_PLAYBACK_SPEED=10
const COLORS: Record<string, string> = {'Path-A':'#00658a','Path-B':'#a34b00'}
function zoneColor(speed:number){if(speed<4)return{fill:'rgba(47,125,61,.46)',stroke:'#245f30'};if(speed<7)return{fill:'rgba(210,158,24,.5)',stroke:'#8f6808'};return{fill:'rgba(190,55,48,.48)',stroke:'#8f2925'}}
export function windDirectionBounds(directionDeg:number,directionSD:number){
  return[(directionDeg-directionSD+360)%360,(directionDeg+directionSD)%360] as const
}
function pointInsideWindZone(x:number,y:number,zone:WindZone){
  const dx=x-zone.cx,dy=y-zone.cy,angle=Math.atan2(dy,dx),phase=zone.shapeSeed??0
  const boundary=zone.radius*(1+.13*Math.sin(3*angle+phase)+.07*Math.sin(5*angle-phase*.4))
  return dx*dx+dy*dy<=boundary*boundary
}
function segmentLengthInsideWindZone(start:Route['waypoints'][number],end:Route['waypoints'][number],zone:WindZone){
  const dx=end.x-start.x,dy=end.y-start.y,length=Math.hypot(dx,dy)
  if(length<1e-9)return 0
  const steps=Math.max(24,Math.ceil(length/20));let inside=0
  for(let step=0;step<steps;step++){
    const ratio=(step+.5)/steps
    if(pointInsideWindZone(start.x+dx*ratio,start.y+dy*ratio,zone))inside++
  }
  return inside/steps*length
}
export function routeRelativeWindAngle(route:Route,zone:WindZone){
  let parallel=0,cross=0,affectedLength=0
  for(let index=0;index<route.waypoints.length-1;index++){
    const start=route.waypoints[index],end=route.waypoints[index+1],length=segmentLengthInsideWindZone(start,end,zone)
    if(length<=0)continue
    const heading=Math.atan2(end.y-start.y,end.x-start.x)*180/Math.PI
    const relative=((zone.directionDeg-heading+540)%360-180)*Math.PI/180
    parallel+=Math.cos(relative)*length;cross+=Math.abs(Math.sin(relative))*length;affectedLength+=length
  }
  return affectedLength>0?Math.atan2(cross,parallel)*180/Math.PI:null
}
function compassName(bearing:number){
  return['北','东北','东','东南','南','西南','西','西北'][Math.round(((bearing%360)+360)%360/45)%8]
}
function windMovementText(directionDeg:number){
  const toward=(90-directionDeg+360)%360,from=(toward+180)%360
  return `从${compassName(from)}吹向${compassName(toward)}`
}
function relativeEffect(angle:number){return angle<60?'偏顺风':angle>120?'偏逆风':'侧风'}
function fillTextWithSoftHalo(ctx:CanvasRenderingContext2D,text:string,x:number,y:number,color='#17313c'){
  ctx.save();ctx.shadowColor='rgba(255,255,255,.96)';ctx.shadowBlur=3;ctx.shadowOffsetX=0;ctx.shadowOffsetY=0;ctx.fillStyle=color;ctx.fillText(text,x,y);ctx.restore()
}
function windArrowPath(ctx:CanvasRenderingContext2D,x:number,y:number,angle:number,length:number){
  const ex=x+Math.cos(angle)*length,ey=y+Math.sin(angle)*length,head=4.5
  ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(ex,ey)
  ctx.moveTo(ex,ey);ctx.lineTo(ex-Math.cos(angle-.58)*head,ey-Math.sin(angle-.58)*head)
  ctx.moveTo(ex,ey);ctx.lineTo(ex-Math.cos(angle+.58)*head,ey-Math.sin(angle+.58)*head)
}
function drawMeanWindArrow(ctx:CanvasRenderingContext2D,x:number,y:number,angle:number,length:number){
  ctx.save();ctx.lineCap='round';ctx.lineJoin='round';ctx.setLineDash([])
  ctx.strokeStyle='rgba(255,255,255,.92)';ctx.lineWidth=5.5;windArrowPath(ctx,x,y,angle,length);ctx.stroke()
  ctx.strokeStyle='#102d38';ctx.lineWidth=2.5;windArrowPath(ctx,x,y,angle,length);ctx.stroke()
  ctx.fillStyle='#102d38';ctx.beginPath();ctx.arc(x,y,2.7,0,Math.PI*2);ctx.fill();ctx.restore()
}
function drawWindDirectionIndicator(ctx:CanvasRenderingContext2D,x:number,y:number,directionDeg:number,directionSD:number,isForecast:boolean,radius:number){
  const angle=-directionDeg*Math.PI/180,arrowLength=Math.max(28,Math.min(43,radius*.34))
  if(isForecast&&directionSD>0){
    const[minDirection,maxDirection]=windDirectionBounds(directionDeg,directionSD)
    const start=-maxDirection*Math.PI/180,end=-minDirection*Math.PI/180,inner=Math.max(11,arrowLength*.32),outer=arrowLength+9
    ctx.save();ctx.lineCap='round';ctx.lineJoin='round'
    ctx.beginPath();ctx.arc(x,y,inner,start,end);ctx.lineTo(x+Math.cos(end)*outer,y+Math.sin(end)*outer);ctx.arc(x,y,outer,end,start,true);ctx.closePath()
    ctx.fillStyle='rgba(255,255,255,.68)';ctx.fill()
    ctx.strokeStyle='rgba(16,45,56,.8)';ctx.lineWidth=1.6;ctx.setLineDash([4,3])
    for(const boundary of[start,end]){ctx.beginPath();ctx.moveTo(x+Math.cos(boundary)*inner,y+Math.sin(boundary)*inner);ctx.lineTo(x+Math.cos(boundary)*outer,y+Math.sin(boundary)*outer);ctx.stroke()}
    ctx.beginPath();ctx.arc(x,y,outer,start,end);ctx.stroke();ctx.restore()
  }
  drawMeanWindArrow(ctx,x,y,angle,arrowLength)
}
function projectToRoute(x:number,y:number,waypoints:TrialDetail['routes'][number]['waypoints']){
  let best={x,y,distance:Infinity}
  for(let index=0;index<waypoints.length-1;index++){
    const start=waypoints[index],end=waypoints[index+1],vx=end.x-start.x,vy=end.y-start.y
    const ratio=Math.max(0,Math.min(1,((x-start.x)*vx+(y-start.y)*vy)/Math.max(vx*vx+vy*vy,1e-9)))
    const px=start.x+ratio*vx,py=start.y+ratio*vy,distance=(x-px)**2+(y-py)**2
    if(distance<best.distance)best={x:px,y:py,distance}
  }
  return{x:best.x,y:best.y}
}
interface Props{trial:TrialDetail;frame?:Frame;displayLayer:number;trail:Frame[];activePathId?:string;flightTimeSeconds?:number;interactionDisabled?:boolean;onMapInteract?:()=>void;onWindInspect?:()=>void}

export function SimulationCanvas({trial,frame,displayLayer,trail,activePathId,flightTimeSeconds,interactionDisabled=false,onMapInteract,onWindInspect}:Props){
  const wrapperRef=useRef<HTMLDivElement>(null),canvasRef=useRef<HTMLCanvasElement>(null),focusDroneImageRef=useRef<HTMLImageElement|null>(null),basemapRef=useRef<HTMLImageElement|null>(null)
  const [viewport,setViewport]=useState({width:0,height:0}),[imageReady,setImageReady]=useState(0),[mapReady,setMapReady]=useState(false),[zoom,setZoom]=useState(1),[pan,setPan]=useState({x:0,y:0})
  const [hoveredWind,setHoveredWind]=useState<{x:number;y:number;zone:WindZone;routes:{pathId:string;angle:number}[]}>()
  const windHitAreasRef=useRef<{x:number;y:number;radius:number;zone:WindZone;routes:{pathId:string;angle:number}[]}[]>([])
  const dragRef=useRef<{pointerId:number;x:number;y:number;panX:number;panY:number}|null>(null)
  const changeZoom=(next:number)=>setZoom(Math.max(1,Math.min(3,Math.round(next*10)/10)))
  useEffect(()=>{const el=wrapperRef.current;if(!el)return;const update=()=>{const r=el.getBoundingClientRect();setViewport({width:Math.round(r.width),height:Math.round(r.height)})};update();const observer=new ResizeObserver(update);observer.observe(el);return()=>observer.disconnect()},[])
  useEffect(()=>{const focusImage=new Image();focusImage.src='/assets/uav_pic.png';focusImage.onload=()=>{focusDroneImageRef.current=focusImage;setImageReady(value=>value+1)}},[])
  useEffect(()=>{const image=new Image();image.src='/assets/haidian-basemap-3_5km.jpg';image.onload=()=>{basemapRef.current=image;setMapReady(true)}},[])
  useEffect(()=>{setZoom(1);setPan({x:0,y:0})},[trial.trialId])
  useEffect(()=>{setHoveredWind(undefined)},[trial.trialId,displayLayer,zoom,pan.x,pan.y])
  useEffect(()=>{
    const canvas=canvasRef.current;if(!canvas||viewport.width<1||viewport.height<1)return
    const dpr=Math.max(window.devicePixelRatio||1,2),width=viewport.width,height=viewport.height
    canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr)
    const ctx=canvas.getContext('2d')!;ctx.setTransform(dpr,0,0,dpr,0,0)
    const allPoints=trial.routes.flatMap(r=>r.waypoints),focusPoints=trial.routes.filter(route=>route.taskRole==='focused_candidate').flatMap(route=>route.waypoints),allZones=trial.windLayers.flatMap(layer=>layer.zones),zones=trial.windLayers.find(l=>l.layer===displayLayer)?.zones??[]
    const scenePoints=[...allPoints,...allZones.flatMap(zone=>[{x:zone.cx-zone.radius*1.2,y:zone.cy-zone.radius*1.2},{x:zone.cx+zone.radius*1.2,y:zone.cy+zone.radius*1.2}])]
    const centeredPoints=focusPoints.length?focusPoints:allPoints,centerX=(Math.min(...centeredPoints.map(point=>point.x))+Math.max(...centeredPoints.map(point=>point.x)))/2,centerY=(Math.min(...centeredPoints.map(point=>point.y))+Math.max(...centeredPoints.map(point=>point.y)))/2
    const rawHalfX=Math.max(...scenePoints.map(point=>Math.abs(point.x-centerX))),rawHalfY=Math.max(...scenePoints.map(point=>Math.abs(point.y-centerY))),padding=Math.max(140,Math.max(rawHalfX,rawHalfY)*.1)
    const minX=centerX-rawHalfX-padding,maxX=centerX+rawHalfX+padding,minY=centerY-rawHalfY-padding,maxY=centerY+rawHalfY+padding
    const scale=Math.min(width/(maxX-minX),height/(maxY-minY))*zoom,offsetX=(width-(maxX-minX)*scale)/2+pan.x,offsetY=(height-(maxY-minY)*scale)/2+pan.y
    const sx=(x:number)=>offsetX+(x-minX)*scale,sy=(y:number)=>offsetY+(maxY-y)*scale
    ctx.fillStyle='#dbe7ec';ctx.fillRect(0,0,width,height)
    if(basemapRef.current)ctx.drawImage(basemapRef.current,offsetX,offsetY,(maxX-minX)*scale,(maxY-minY)*scale)
    ctx.strokeStyle='rgba(28,58,69,.08)';ctx.lineWidth=1
    for(let x=offsetX%40-40;x<width;x+=40){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,height);ctx.stroke()}for(let y=offsetY%40-40;y<height;y+=40){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(width,y);ctx.stroke()}
    windHitAreasRef.current=zones.map(zone=>{const x=sx(zone.cx),y=sy(zone.cy),r=zone.radius*scale,indicatorY=y-Math.max(12,Math.min(20,r*.14)),routes=trial.routes.filter(route=>route.taskRole==='focused_candidate').map(route=>({pathId:route.pathId,angle:routeRelativeWindAngle(route,zone)})).filter((item):item is {pathId:string;angle:number}=>item.angle!==null);return{x,y:indicatorY,radius:52,zone,routes}})
    zones.forEach(zone=>{const color=zoneColor(zone.meanSpeed),x=sx(zone.cx),y=sy(zone.cy),r=zone.radius*scale,seed=zone.shapeSeed??0;ctx.fillStyle=color.fill;ctx.strokeStyle=color.stroke;ctx.lineWidth=2;ctx.setLineDash(zone.isForecast?[5,3]:[]);ctx.beginPath();for(let i=0;i<=72;i++){const a=i/72*Math.PI*2,rr=r*(1+.13*Math.sin(3*a+seed)+.07*Math.sin(5*a-seed*.4)),px=x+Math.cos(a)*rr,py=y+Math.sin(a)*rr;i?ctx.lineTo(px,py):ctx.moveTo(px,py)}ctx.closePath();ctx.fill();ctx.stroke();ctx.setLineDash([]);const indicatorY=y-Math.max(12,Math.min(20,r*.14));drawWindDirectionIndicator(ctx,x,indicatorY,zone.directionDeg,zone.directionSD,zone.isForecast,r)})
    trial.routes.forEach(route=>{const active=route.pathId===activePathId;ctx.strokeStyle=COLORS[route.pathId]??'#17313c';ctx.globalAlpha=active?1:.92;ctx.lineWidth=active?5:3;ctx.beginPath();route.waypoints.forEach((p,i)=>i?ctx.lineTo(sx(p.x),sy(p.y)):ctx.moveTo(sx(p.x),sy(p.y)));ctx.stroke();ctx.globalAlpha=1})
    const unique=new Map<string,typeof allPoints[number]>();allPoints.forEach(p=>unique.set(`${p.waypoint_id}:${p.x}:${p.y}`,p))
    unique.forEach(p=>{const x=sx(p.x),y=sy(p.y),side=x>width-90?-1:1,dx=side*10;ctx.fillStyle='#173b49';ctx.strokeStyle='#fff';ctx.lineWidth=2;ctx.beginPath();ctx.arc(x,y,5,0,Math.PI*2);ctx.fill();ctx.stroke();ctx.font='700 15px "IBM Plex Mono",monospace';const w=ctx.measureText(p.waypoint_id).width,lx=side>0?x+dx:x+dx-w;ctx.textAlign='left';ctx.textBaseline='middle';fillTextWithSoftHalo(ctx,p.waypoint_id,lx,y+17)})
    if(trail.length>1){ctx.strokeStyle='#c8ff61';ctx.lineWidth=2;ctx.beginPath();trail.forEach((f,i)=>i?ctx.lineTo(sx(f.x),sy(f.y)):ctx.moveTo(sx(f.x),sy(f.y)));ctx.stroke()}
    const drawDrone=(x:number,y:number,id:string)=>{const width=62,height=42,image=focusDroneImageRef.current;if(image)ctx.drawImage(image,70,30,850,570,x-width/2,y-height/2,width,height);else{ctx.fillStyle='#b56a24';ctx.beginPath();ctx.arc(x,y,7,0,Math.PI*2);ctx.fill()}ctx.font='700 15px "IBM Plex Mono",monospace';ctx.textAlign='center';ctx.textBaseline='middle';fillTextWithSoftHalo(ctx,id,x,y+height/2+8)}
    const initialDrones=trial.initialDrones??[]
    const fallbackStart=trial.routes.find(route=>route.pathId==='Path-A')?.waypoints[0]??trial.routes[0]?.waypoints[0]
    let drone=frame?{x:frame.x,y:frame.y}:initialDrones.find(d=>d.id===1)??fallbackStart
    const activeRoute=trial.routes.find(route=>route.pathId===activePathId)
    if(frame&&activeRoute)drone=projectToRoute(frame.x,frame.y,activeRoute.waypoints)
    if(drone){const dx=sx(drone.x),dy=sy(drone.y);drawDrone(dx,dy,'U01');ctx.fillStyle=COLORS[activePathId??'Path-A']??'#173b49';ctx.strokeStyle='#fff';ctx.lineWidth=1.5;ctx.beginPath();ctx.arc(dx,dy,4,0,Math.PI*2);ctx.fill();ctx.stroke()}
    zones.forEach(zone=>{const x=sx(zone.cx),y=sy(zone.cy)-Math.max(30,Math.min(48,zone.radius*scale*.22));ctx.font='800 15px "IBM Plex Mono",monospace';ctx.textAlign='center';ctx.textBaseline='middle';fillTextWithSoftHalo(ctx,zone.zoneId,x,y,'#173b49')})
  },[trial,frame,displayLayer,trail,viewport,imageReady,mapReady,zoom,pan,activePathId])
  return <div className={`simulation-canvas${hoveredWind?' wind-hover':''}${interactionDisabled?' interaction-disabled':''}`} ref={wrapperRef} onWheel={event=>{event.preventDefault();if(!interactionDisabled)changeZoom(zoom+(event.deltaY<0?.2:-.2))}}
    onPointerDown={event=>{if(interactionDisabled||(event.target as HTMLElement).closest('.map-zoom'))return;onMapInteract?.();const rect=event.currentTarget.getBoundingClientRect(),x=event.clientX-rect.left,y=event.clientY-rect.top,hit=windHitAreasRef.current.map(area=>({...area,distance:Math.hypot(x-area.x,y-area.y)})).filter(area=>area.distance<=area.radius).sort((a,b)=>a.distance-b.distance)[0];if(hit){onWindInspect?.();setHoveredWind({x,y,zone:hit.zone,routes:hit.routes});return}setHoveredWind(undefined);dragRef.current={pointerId:event.pointerId,x:event.clientX,y:event.clientY,panX:pan.x,panY:pan.y};event.currentTarget.setPointerCapture(event.pointerId)}}
    onPointerMove={event=>{if(interactionDisabled){setHoveredWind(undefined);return}const drag=dragRef.current;if(drag&&drag.pointerId===event.pointerId){setHoveredWind(undefined);setPan({x:drag.panX+event.clientX-drag.x,y:drag.panY+event.clientY-drag.y});return}const rect=event.currentTarget.getBoundingClientRect(),x=event.clientX-rect.left,y=event.clientY-rect.top,hit=windHitAreasRef.current.map(area=>({...area,distance:Math.hypot(x-area.x,y-area.y)})).filter(area=>area.distance<=area.radius).sort((a,b)=>a.distance-b.distance)[0];if(hit)onWindInspect?.();setHoveredWind(hit?{x,y,zone:hit.zone,routes:hit.routes}:undefined)}}
    onPointerUp={event=>{if(dragRef.current?.pointerId===event.pointerId)dragRef.current=null}} onPointerCancel={()=>{dragRef.current=null;setHoveredWind(undefined)}} onPointerLeave={()=>setHoveredWind(undefined)}>
    <canvas ref={canvasRef} aria-label="无人机路线和风场仿真画布"/>
    <div className="map-zoom" aria-label="地图缩放控件">
      <button type="button" onClick={()=>changeZoom(zoom+.2)} disabled={interactionDisabled||zoom>=3} aria-label="放大地图">＋</button>
      <button type="button" className="zoom-value" disabled={interactionDisabled} onClick={()=>{changeZoom(1);setPan({x:0,y:0})}} title="恢复完整地图">{zoom.toFixed(1)}×</button>
      <button type="button" onClick={()=>changeZoom(zoom-.2)} disabled={interactionDisabled||zoom<=1} aria-label="缩小地图">−</button>
    </div>
    <div className="map-drag-hint">拖动地图 · 滚轮缩放</div>
    {hoveredWind&&<div className="wind-tooltip" style={{left:Math.max(10,Math.min(hoveredWind.x+14,viewport.width-244)),top:Math.max(10,Math.min(hoveredWind.y+14,viewport.height-150))}}><strong>{hoveredWind.zone.zoneId} 风场</strong><span>风速 {hoveredWind.zone.meanSpeed.toFixed(1)}{hoveredWind.zone.isForecast?` ± ${hoveredWind.zone.speedSD.toFixed(1)}`:''} m/s</span><span>风{windMovementText(hoveredWind.zone.directionDeg)}{hoveredWind.zone.isForecast?`，方向范围 ±${hoveredWind.zone.directionSD.toFixed(0)}°`:''}</span>{hoveredWind.routes.map(route=><span key={route.pathId}><i className={route.pathId==='Path-A'?'path-a':'path-b'}/>{route.pathId} 相对航向角 {route.angle.toFixed(0)}°（{relativeEffect(route.angle)}）</span>)}</div>}
    {flightTimeSeconds!==undefined&&activePathId&&<div className="flight-time-clock" role="timer" aria-label={`实际飞行时间 ${flightTimeSeconds.toFixed(1)} 秒`}><span>实际飞行时间</span><strong>{flightTimeSeconds.toFixed(1)} <small>秒</small></strong></div>}
  </div>
}
