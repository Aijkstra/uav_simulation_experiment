import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { api } from './api'
import { SIMULATION_PLAYBACK_SPEED, SimulationCanvas } from './SimulationCanvas'
import type { Action, AssignedTrial, FormalSimulationResponse, Frame, SimulationResponse } from './types'
import './styles.css'

type Phase='start'|'instructions'|'practice-intro'|'comprehension'|'practice'|'formal-intro'|'trial'|'block'|'questionnaire'|'complete'
const PROFESSIONAL_FIELDS=['工程与技术','计算机与信息科学','管理与经济','心理与认知科学','自然科学','人文与社会科学','其他'] as const
type ProfessionalField=typeof PROFESSIONAL_FIELDS[number]
const OTHER_SELECTION='其他，请说明'
const SIMULATION_TRIGGER_OPTIONS=['未来风场的变化范围较大时','不同调度方案可能造成较大的飞行时间差异时','路线、风区和时间层之间的关系较复杂时','第一眼看不出哪个方案更合适时','我对初步判断的把握较低时','只要时间允许，我通常都会仔细推演或比较方案','没有固定规律',OTHER_SELECTION]
const DECISION_INFORMATION_OPTIONS=['风速大小','风向与飞行路线的相对关系','风场变化范围','T0、T1、T2时间层变化','无风benchmark','无人机进入和离开W1、W2的时间','Path-A与Path-B的路线差异','比较多个调度方案的可能结果','主要依据一个关键线索快速判断','主要依靠整体直觉',OTHER_SELECTION]
export function responseOptionsDisabled(phase:Phase,practiceFeedback:string,busy=false){
  return busy||(phase==='practice'&&!!practiceFeedback)
}
export function practiceFlightTime(elapsedTime:number,action:Action,delaySeconds:number){
  return Math.max(0,elapsedTime-(action==='delay'?delaySeconds:0))
}
export function simulationFrameForPhase(phase:Phase,frame:Frame|undefined){
  return phase==='practice'?frame:undefined
}
export const FORMAL_TRIAL_SECONDS=90
export const RESULT_REVIEW_SECONDS=30
export function formatCountdown(seconds:number){
  const safe=Math.max(0,Math.ceil(seconds))
  return `${String(Math.floor(safe/60)).padStart(2,'0')}:${String(safe%60).padStart(2,'0')}`
}
export function formalTimeoutAllowed(decisionCommitted:boolean,submissionInFlight:boolean){
  return !decisionCommitted&&!submissionInFlight
}
const ACTIONS:{id:Action;label:string;detail:string}[]=[
  {id:'release',label:'立即放行',detail:'现在沿 Path-A 起飞'},
  {id:'delay',label:'延迟起飞',detail:'等待 120 秒后沿 Path-A 起飞'},
  {id:'reroute',label:'改道飞行',detail:'现在沿 Path-B 起飞'},
]
export const TASK_GOAL='选择使焦点无人机预计飞行时间最短的调度方案'
export const TASK_GOAL_NOTE='飞行时间只计算从起飞到抵达目的地的时长；地面等待单独显示，不计入飞行时间。'
export const COMPREHENSION_QUESTIONS=[
  {id:'wind',text:'1. 风区中的实线箭头和浅色扇形分别表示什么？',answer:'B',feedback:'第 1 题：实线箭头表示平均风向，浅色扇形表示风向可能出现的范围；扇形越宽，风向越不确定。',options:[['A','实线箭头表示无人机朝向，浅色扇形表示航路范围'],['B','实线箭头表示平均风向，浅色扇形表示风向可能出现的范围'],['C','实线箭头和浅色扇形分别表示两个飞行高度的风向']]},
  {id:'layers',text:'2. T0、T1和T2分别对应哪三个风场时间层？',answer:'C',feedback:'第 2 题：T0为第0至120秒，T1为第120至240秒，T2为第240至480秒的风场。',options:[['A','三个不同的飞行高度'],['B','三架无人机各自所在的时间'],['C','T0为第0至120秒，T1为第120至240秒，T2为第240至480秒的风场']]},
  {id:'delay',text:'3. “立即沿 Path-A 起飞”和“等待 120 秒后沿 Path-A 起飞”的区别是什么？',answer:'A',feedback:'第 3 题：两种方案都沿 Path-A 飞行，区别仅在于起飞时间：一种在 T0 起飞，另一种在 T1 起飞。',options:[['A','两种方案沿同一条航路飞行，但起飞时间不同'],['B','两种方案的起飞时间相同，但目的地不同'],['C','两种方案使用的无人机数量不同']]},
  {id:'goal',text:'4. 本任务的调度目标是什么？',answer:'B',feedback:'第 4 题：本任务只比较从起飞到抵达的飞行时间。延迟方案的 120 秒地面等待时间不计入调度目标。',options:[['A','选择从 T0 到送达目的地的总时间最短的方案，包括地面等待时间'],['B','选择焦点无人机预计飞行时间最短的方案；飞行时间只计算从起飞到抵达目的地的时长'],['C','优先减少地图中所有无人机的电量消耗']]},
  {id:'flight_layers',text:'5. 某无人机在场景时间第120秒起飞，飞行时间约为144秒，它在飞行过程中先后经历哪些风场时间层？',answer:'C',feedback:'第 5 题：无人机在第120秒进入 T1 时起飞，并在约第264秒抵达，因此飞行过程将经历 T1 和 T2。',options:[['A','仅 T1'],['B','T0 和 T1'],['C','T1 和 T2']]},
  {id:'wind_effect',text:'6. 风场为什么可能改变无人机的飞行时间？',answer:'B',feedback:'第 6 题：风会改变无人机的地速：顺风通常缩短飞行时间，逆风和侧风通常延长飞行时间。',options:[['A','风场会改变无人机的目的地'],['B','顺风、逆风和侧风会影响无人机沿航路方向的有效前进速度，从而改变飞行时间。'],['C','风场只会影响地面等待时间，不会影响飞行过程']]},
] as const

export function comprehensionFeedback(answers:Record<string,string>){
  return COMPREHENSION_QUESTIONS.filter(question=>answers[question.id]!==question.answer).map(question=>question.feedback)
}

function frameAtTime(frames:Frame[],time:number):Frame|undefined{
  if(!frames.length)return undefined
  if(time<=frames[0].time)return frames[0]
  if(time>=frames[frames.length-1].time)return frames[frames.length-1]
  let low=0,high=frames.length-1
  while(high-low>1){const middle=(low+high)>>1;if(frames[middle].time<=time)low=middle;else high=middle}
  const start=frames[low],end=frames[high],ratio=(time-start.time)/Math.max(end.time-start.time,1e-9)
  return {...start,time,x:start.x+(end.x-start.x)*ratio,y:start.y+(end.y-start.y)*ratio,
    groundSpeed:start.groundSpeed+(end.groundSpeed-start.groundSpeed)*ratio,
    batteryWh:start.batteryWh+(end.batteryWh-start.batteryWh)*ratio,
    energyWh:start.energyWh+(end.energyWh-start.energyWh)*ratio,
    eta:start.eta+(end.eta-start.eta)*ratio,
  }
}

type PracticeGuideSimulationState={played:boolean;paused:boolean;resumed:boolean;switched:boolean;timerSeen:boolean}
export function PracticeGuide({step,openedLayers,simulationState,mapInteracted,windInspected,onNext}:{step:number;openedLayers:number[];simulationState:PracticeGuideSimulationState;mapInteracted:boolean;windInspected:boolean;hasInitialDecision:boolean;feedbackVisible:boolean;onNext:()=>void}){
  const titles=['','任务目标','候选方案','查看候选航路','切换风场时间层','查看风场信息','查看无风基准','运行练习仿真','引导完成']
  const guideRef=useRef<HTMLElement>(null)
  const [position,setPosition]=useState<{top:number;left:number;placement:string}>()
  useLayoutEffect(()=>{
    const guide=guideRef.current
    const targetName=step===1?'goal':step===2?'actions':step===3||step===5?'map':step===4?'layers':step===6?'benchmark':'simulator'
    const target=document.querySelector<HTMLElement>(`[data-guide-target="${targetName}"]`)
    if(!guide||!target)return
    const place=()=>{
      const targetRect=target.getBoundingClientRect(),guideRect=guide.getBoundingClientRect(),gap=14,margin=12
      const candidates=[
        {placement:'right',top:targetRect.top+(targetRect.height-guideRect.height)/2,left:targetRect.right+gap},
        {placement:'left',top:targetRect.top+(targetRect.height-guideRect.height)/2,left:targetRect.left-guideRect.width-gap},
        {placement:'bottom',top:targetRect.bottom+gap,left:targetRect.left+(targetRect.width-guideRect.width)/2},
        {placement:'top',top:targetRect.top-guideRect.height-gap,left:targetRect.left+(targetRect.width-guideRect.width)/2},
      ]
      const fits=(item:{top:number;left:number})=>item.left>=margin&&item.top>=margin&&item.left+guideRect.width<=window.innerWidth-margin&&item.top+guideRect.height<=window.innerHeight-margin
      const selected=candidates.find(fits)??candidates.reduce<{placement:string;top:number;left:number;score:number}>((best,item)=>{
        const visibleWidth=Math.max(0,Math.min(window.innerWidth-margin,item.left+guideRect.width)-Math.max(margin,item.left))
        const visibleHeight=Math.max(0,Math.min(window.innerHeight-margin,item.top+guideRect.height)-Math.max(margin,item.top))
        return visibleWidth*visibleHeight>best.score?{...item,score:visibleWidth*visibleHeight}:best
      },{...candidates[0],score:-1})
      setPosition({placement:selected.placement,left:Math.min(Math.max(margin,selected.left),window.innerWidth-guideRect.width-margin),top:Math.min(Math.max(margin,selected.top),window.innerHeight-guideRect.height-margin)})
    }
    place()
    const observer=new ResizeObserver(place);observer.observe(target);observer.observe(guide)
    window.addEventListener('resize',place);window.addEventListener('scroll',place,true)
    return()=>{observer.disconnect();window.removeEventListener('resize',place);window.removeEventListener('scroll',place,true)}
  },[step])
  const guideStyle=position?{top:position.top,left:position.left} as CSSProperties:undefined
  return <aside ref={guideRef} style={guideStyle} data-placement={position?.placement} className={`practice-guide${position?' positioned':''}`} aria-live="polite"><span>第 {step}/8 步</span><strong>{titles[step]}</strong>
    {step===1&&<><p>每轮需要从三种方案中选择无人机预计空中飞行时间最短的方案。</p><button onClick={onNext}>下一步</button></>}
    {step===2&&<><p>候选方案是立即沿Path-A起飞、等待120秒后沿Path-A起飞、立即沿Path-B起飞。练习可以运行仿真。</p><button onClick={onNext}>下一步</button></>}
    {step===3&&<><p>请在地图上点击或拖动一次，查看蓝色Path-A和橙色Path-B两条候选航路。</p><button disabled={!mapInteracted} onClick={onNext}>下一步</button></>}
    {step===4&&<><p>请依次实际打开T0、T1和T2。无人机可能跨越多个时间层，不能只看起飞时刻。</p><small>已打开：{openedLayers.length?[...openedLayers].sort().map(layer=>`T${layer}`).join('、'):'尚未打开'}</small><button disabled={openedLayers.length<3} onClick={onNext}>下一步</button></>}
    {step===5&&<><p>请将鼠标移到任一风区或箭头上，查看风速、平均风向、可能范围和相对航向信息。</p><button disabled={!windInspected} onClick={onNext}>下一步</button></>}
    {step===6&&<><p>无风基准提供对应路线的无风飞行时间以及进入、离开W1/W2的参考时间。</p><button onClick={onNext}>下一步</button></>}
    {step===7&&<><p>请点击任一方案的仿真按钮并开始播放仿真。仿真以10倍速度播放，计时器显示实际飞行时间；不能拖动或跳转进度。</p><button disabled={!simulationState.played} onClick={onNext}>下一步</button></>}
    {step===8&&<p>任务引导已完成，接下来请你认真观察任务场景并使用仿真功能，并提交你认为飞行时间最短的方案。</p>}
  </aside>
}

export default function App(){
  const [phase,setPhase]=useState<Phase>('start'),[participantId,setParticipantId]=useState('')
  const [trial,setTrial]=useState<AssignedTrial>(),[displayLayer,setDisplayLayer]=useState(0)
  const [intermediate,setIntermediate]=useState(''),[decision,setDecision]=useState<Action>()
  const [forecastPrediction,setForecastPrediction]=useState('')
  const [answers,setAnswers]=useState<Record<string,string>>({}),[message,setMessage]=useState(''),[error,setError]=useState('')
  const [busy,setBusy]=useState(false),[blockRatings,setBlockRatings]=useState<{mentalEffort?:number;fatigue?:number;taskDifficulty?:number;mentalSimulationStrategy?:number;multiActionComparisonStrategy?:number;simpleCueStrategy?:number}>({})
  const [questionnaire,setQuestionnaire]=useState<{major:ProfessionalField|'';uavExperience:ExperienceLevel;aviationCourse:ExperienceLevel;systemManagementExperience:ExperienceLevel;gameExperience:ExperienceLevel;mapAbility?:number;sustainedAttention?:number;overallEffort?:number;scenarioComprehension?:number;windRouteReliance?:number;windRelationImportance?:number;mentalSimulationUse?:number;perceivedRealism?:number;uncertaintyAnalysisEffort?:number;intervenabilityComparisonEffort?:number;simulationTriggerSelections:string[];simulationTriggerOther:string;decisionInformationSelections:string[];decisionInformationOther:string;additionalInformation:string}>({major:'',uavExperience:'',aviationCourse:'',systemManagementExperience:'',gameExperience:'',mapAbility:undefined,sustainedAttention:undefined,overallEffort:undefined,scenarioComprehension:undefined,windRouteReliance:undefined,windRelationImportance:undefined,mentalSimulationUse:undefined,perceivedRealism:undefined,uncertaintyAnalysisEffort:undefined,intervenabilityComparisonEffort:undefined,simulationTriggerSelections:[],simulationTriggerOther:'',decisionInformationSelections:[],decisionInformationOther:'',additionalInformation:''})
  const [practiceNo,setPracticeNo]=useState(1),[practiceFeedback,setPracticeFeedback]=useState('')
  const [practiceSimulation,setPracticeSimulation]=useState<SimulationResponse>(),[practiceTime,setPracticeTime]=useState(0),[practicePlaying,setPracticePlaying]=useState(false),[practiceSimBusy,setPracticeSimBusy]=useState(false)
  const [practiceGuideStep,setPracticeGuideStep]=useState(0),[practiceGuideLayers,setPracticeGuideLayers]=useState<number[]>([])
  const [practiceGuideMapInteracted,setPracticeGuideMapInteracted]=useState(false),[practiceGuideWindInspected,setPracticeGuideWindInspected]=useState(false)
  const [practiceGuideSimulation,setPracticeGuideSimulation]=useState({played:false,paused:false,resumed:false,switched:false,timerSeen:false})
  const [formalSimulation,setFormalSimulation]=useState<FormalSimulationResponse>(),[formalTime,setFormalTime]=useState(0),[formalPlaying,setFormalPlaying]=useState(false)
  const [decisionCommitted,setDecisionCommitted]=useState(false),[formalSimBusy,setFormalSimBusy]=useState(false)
  const [remainingSeconds,setRemainingSeconds]=useState(FORMAL_TRIAL_SECONDS)
  const [resultSeconds,setResultSeconds]=useState(RESULT_REVIEW_SECONDS),[answerTimedOut,setAnswerTimedOut]=useState(false)
  const [perceivedUncertainty,setPerceivedUncertainty]=useState<number>(),[perceivedIntervenability,setPerceivedIntervenability]=useState<number>(),[flightTimeConfidence,setFlightTimeConfidence]=useState<number>(),[actionChoiceConfidence,setActionChoiceConfidence]=useState<number>()
  const trialStarted=useRef(0),predictionAnsweredAt=useRef(0),intermediateAnsweredAt=useRef(0),decisionAnsweredAt=useRef(0),ratingStarted=useRef(0)
  const activeLayer=useRef(0),layerEnteredAt=useRef(0),layerDwellMs=useRef([0,0,0]),layerSequence=useRef<number[]>([]),layerSwitchCount=useRef(0),firstFutureLayerOpenedAt=useRef<number|undefined>(undefined),layerTrackingFrozen=useRef(false)
  const predictionChangeCount=useRef(0),intermediateChangeCount=useRef(0),decisionChangeCount=useRef(0)
  const playbackClock=useRef(0)
  const submissionInFlight=useRef(false),decisionCommittedRef=useRef(false)

  function startLayerTracking(startedAt:number){
    activeLayer.current=0;layerEnteredAt.current=startedAt;layerDwellMs.current=[0,0,0];layerSequence.current=[0];layerSwitchCount.current=0;firstFutureLayerOpenedAt.current=undefined;layerTrackingFrozen.current=false
    predictionChangeCount.current=0;intermediateChangeCount.current=0;decisionChangeCount.current=0
  }
  function freezeLayerTracking(endedAt:number){
    if(!layerTrackingFrozen.current){
      layerDwellMs.current[activeLayer.current]+=Math.max(0,endedAt-layerEnteredAt.current)
      layerEnteredAt.current=endedAt;layerTrackingFrozen.current=true
    }
    return [...layerDwellMs.current]
  }
  function recordLayerSelection(layer:number,now:number){
    if(layer===activeLayer.current||layerTrackingFrozen.current)return
    layerDwellMs.current[activeLayer.current]+=Math.max(0,now-layerEnteredAt.current)
    const previousLayer=activeLayer.current
    activeLayer.current=layer;layerEnteredAt.current=now;layerSwitchCount.current+=1;layerSequence.current.push(layer)
    if(layer>0&&firstFutureLayerOpenedAt.current===undefined)firstFutureLayerOpenedAt.current=now
    api.event({eventType:'wind_layer_opened',participantId,trialId:trial?.trialId,eventValue:{layer,previousLayer,layerDwellMs:[...layerDwellMs.current]},clientTimeMs:now})
  }

  useEffect(()=>{
    if(phase!=='practice'||!practicePlaying||!practiceSimulation)return
    const start=practiceSimulation.frames[0]?.time??0,end=practiceSimulation.frames.at(-1)?.time??start
    const speed=SIMULATION_PLAYBACK_SPEED
    playbackClock.current=performance.now()
    let animation=0
    const tick=(now:number)=>{const elapsed=(now-playbackClock.current)/1000;playbackClock.current=now;setPracticeTime(current=>{const next=Math.min(end,current+elapsed*speed);if(next>=end)setPracticePlaying(false);return next});animation=requestAnimationFrame(tick)}
    animation=requestAnimationFrame(tick)
    return()=>cancelAnimationFrame(animation)
  },[phase,practicePlaying,practiceSimulation])
  const practiceFrame=useMemo(()=>practiceSimulation?frameAtTime(practiceSimulation.frames,practiceTime):undefined,[practiceSimulation,practiceTime])
  useEffect(()=>{if(phase==='practice'&&practiceFrame)setDisplayLayer(practiceFrame.actualWindLayer)},[phase,practiceFrame?.actualWindLayer])
  useEffect(()=>{
    if(phase!=='trial'||!formalPlaying||!formalSimulation)return
    const start=formalSimulation.frames[0]?.time??0,end=formalSimulation.frames.at(-1)?.time??start
    const speed=Math.max(SIMULATION_PLAYBACK_SPEED,(end-start)/Math.max(RESULT_REVIEW_SECONDS-1,1))
    playbackClock.current=performance.now()
    let animation=0,completed=false
    const tick=(now:number)=>{const elapsed=(now-playbackClock.current)/1000;playbackClock.current=now;setFormalTime(current=>{const next=Math.min(end,current+elapsed*speed);if(next>=end&&!completed){completed=true;setFormalPlaying(false);api.event({eventType:'formal_simulation_completed',sessionId:formalSimulation.sessionId,participantId,trialId:trial?.trialId,clientTimeMs:performance.now()})}return next});animation=requestAnimationFrame(tick)}
    animation=requestAnimationFrame(tick)
    return()=>cancelAnimationFrame(animation)
  },[phase,formalPlaying,formalSimulation])
  const formalFrame=useMemo(()=>formalSimulation?frameAtTime(formalSimulation.frames,formalTime):undefined,[formalSimulation,formalTime])
  useEffect(()=>{if(phase==='trial'&&formalFrame)setDisplayLayer(formalFrame.actualWindLayer)},[phase,formalFrame?.actualWindLayer])
  useEffect(()=>{
    if(!decisionCommitted||phase!=='trial')return
    setResultSeconds(RESULT_REVIEW_SECONDS)
    const started=performance.now()
    const interval=window.setInterval(()=>{
      setResultSeconds(Math.max(0,Math.ceil(RESULT_REVIEW_SECONDS-(performance.now()-started)/1000)))
    },250)
    return()=>window.clearInterval(interval)
  },[phase,decisionCommitted,trial?.trialId])

  function beginTrial(next:AssignedTrial,id=participantId){
    setTrial(next);setDisplayLayer(0);setForecastPrediction('');setIntermediate('');setDecision(undefined);setPracticeFeedback('')
    setPracticeSimulation(undefined);setPracticeTime(0);setPracticePlaying(false)
    decisionCommittedRef.current=false
    setFormalSimulation(undefined);setFormalTime(0);setFormalPlaying(false);setDecisionCommitted(false);setFormalSimBusy(false)
    setPerceivedUncertainty(undefined);setPerceivedIntervenability(undefined);setFlightTimeConfidence(undefined);setActionChoiceConfidence(undefined);setAnswerTimedOut(false);setResultSeconds(RESULT_REVIEW_SECONDS);predictionAnsweredAt.current=0;intermediateAnsweredAt.current=0;decisionAnsweredAt.current=0;ratingStarted.current=0
    const startedAt=performance.now();trialStarted.current=startedAt;startLayerTracking(startedAt);setPhase('trial')
    api.event({eventType:'trial_entered',participantId:id,trialId:next.trialId,eventValue:{block:next.block,globalOrder:next.globalOrder},clientTimeMs:startedAt})
    api.event({eventType:'wind_layer_opened',participantId:id,trialId:next.trialId,eventValue:{layer:0,previousLayer:null},clientTimeMs:startedAt})
  }
  async function resumeParticipant(id:string){
    const state=await api.participantState(id)
    if(state.status==='completed')throw new Error('该实验编号已经完成，不能再次进入。')
    const next=await api.nextTrial(id)
    if(next.complete){setPhase('questionnaire');return}
    beginTrial(next,id)
    setMessage(`已恢复至第 ${next.block} 组第 ${next.positionInBlock} 轮。`)
  }
  async function begin(){
    const id=participantId.trim()
    if(!id)return setError('请输入实验编号')
    setBusy(true);setError('')
    setParticipantId(id)
    try{await api.createParticipant(id);setPhase('instructions')}
    catch(createError){
      try{await resumeParticipant(id)}
      catch(resumeError){setError(resumeError instanceof Error?resumeError.message:(createError instanceof Error?createError.message:'无法创建或恢复实验'))}
    }
    finally{setBusy(false)}
  }
  async function checkComprehension(){
    if(COMPREHENSION_QUESTIONS.some(question=>!answers[question.id]))return setMessage('请完成全部六道题。')
    const result=await api.comprehension(participantId,answers)
    if(result.passed){setMessage('理解测试通过');setPhase('formal-intro')}else setMessage([`答对 ${result.correctCount}/${result.total}。请根据以下规则提醒重新作答：`,...comprehensionFeedback(answers)].join('\n'))
  }
  async function loadPractice(no:number){
    setBusy(true);setError('')
    try{const item=await api.practice(no);setTrial(item);setPracticeNo(no);setPracticeFeedback('');setPracticeSimulation(undefined);setPracticeTime(0);setPracticePlaying(false);setPracticeSimBusy(false);setPracticeGuideStep(no===1?1:0);setPracticeGuideLayers([]);setPracticeGuideMapInteracted(false);setPracticeGuideWindInspected(false);setPracticeGuideSimulation({played:false,paused:false,resumed:false,switched:false,timerSeen:false});setForecastPrediction('');setIntermediate('');setDecision(undefined);setPerceivedUncertainty(undefined);setPerceivedIntervenability(undefined);setFlightTimeConfidence(undefined);setActionChoiceConfidence(undefined);setDecisionCommitted(false);decisionCommittedRef.current=false;setAnswerTimedOut(false);setDisplayLayer(0);setPhase('practice')}
    catch(e){setError(e instanceof Error?e.message:'无法载入练习')}finally{setBusy(false)}
  }
  async function submitPractice(){
    if(!forecastPrediction||!decision)return setError('请完成飞行时间判断和调度方案选择')
    setBusy(true);setError('')
    try{const result=await api.submitPractice(participantId,practiceNo,forecastPrediction,decision);setPracticeFeedback(result.feedback)}
    catch(e){setError(e instanceof Error?e.message:'练习提交失败')}finally{setBusy(false)}
  }
  async function playPractice(action:Action){
    setPracticeSimBusy(true);setError('')
    try{const previousAction=practiceSimulation?.action;const result=await api.practiceSimulation(practiceNo,action);setPracticeSimulation(result);setPracticeTime(result.frames[0]?.time??0);setDisplayLayer(result.frames[0]?.actualWindLayer??0);setPracticePlaying(true);if(practiceNo===1&&practiceGuideStep>=7)setPracticeGuideSimulation(value=>({...value,played:true,timerSeen:true,switched:value.switched||!!previousAction&&previousAction!==action}))}
    catch(e){setError(e instanceof Error?e.message:'未来仿真载入失败')}finally{setPracticeSimBusy(false)}
  }
  async function continuePractice(){
    if(practiceNo<4)await loadPractice(practiceNo+1);else{setAnswers({});setMessage('');setPhase('comprehension')}
  }
  async function loadNext(id=participantId){
    setBusy(true);setError('')
    try{
      const next=await api.nextTrial(id)
      if(next.complete){setPhase('questionnaire');return}
      beginTrial(next,id)
    }catch(e){setError(e instanceof Error?e.message:'无法载入任务')}finally{setBusy(false)}
  }
  function selectForecastPrediction(answer:string){
    if(responseOptionsDisabled(phase,practiceFeedback,busy)||decisionCommitted)return
    const now=performance.now();if(forecastPrediction&&forecastPrediction!==answer)predictionChangeCount.current+=1;setForecastPrediction(answer)
    if(phase==='trial'){if(!predictionAnsweredAt.current)predictionAnsweredAt.current=now;api.event({eventType:'default_prediction_selected',participantId,trialId:trial?.trialId,eventValue:{answer},clientTimeMs:now})}
  }
  function selectIntermediate(answer:string){
    if(responseOptionsDisabled(phase,practiceFeedback,busy)||decisionCommitted)return
    const now=performance.now();if(intermediate&&intermediate!==answer)intermediateChangeCount.current+=1;setIntermediate(answer)
    if(!intermediateAnsweredAt.current)intermediateAnsweredAt.current=now
    api.event({eventType:'intermediate_selected',participantId,trialId:trial?.trialId,eventValue:{answer},clientTimeMs:now})
  }
  function selectDecision(action:Action){
    if(responseOptionsDisabled(phase,practiceFeedback,busy)||decisionCommitted)return
    const now=performance.now();if(decision&&decision!==action)decisionChangeCount.current+=1;setDecision(action)
    if(phase==='trial'){if(!decisionAnsweredAt.current)decisionAnsweredAt.current=now;api.event({eventType:'decision_selected',participantId,trialId:trial?.trialId,eventValue:{action},clientTimeMs:now})}
  }
  function togglePracticePlayback(){
    setPracticePlaying(value=>{if(practiceNo===1&&practiceGuideStep===8)setPracticeGuideSimulation(state=>({...state,paused:state.paused||value,resumed:state.resumed||!value}));return !value})
  }
  function selectRating(kind:'uncertainty'|'intervenability'|'flightConfidence'|'actionConfidence',value:number){
    if(busy||decisionCommitted)return
    if(!ratingStarted.current)ratingStarted.current=performance.now()
    if(kind==='uncertainty')setPerceivedUncertainty(value)
    else if(kind==='intervenability')setPerceivedIntervenability(value)
    else if(kind==='flightConfidence')setFlightTimeConfidence(value)
    else setActionChoiceConfidence(value)
  }
  function selectLayer(layer:number){
    if((phase==='trial'&&answerTimedOut&&!decisionCommitted)||decisionCommitted)return
    setDisplayLayer(layer)
    const now=performance.now()
    if(phase==='trial')recordLayerSelection(layer,now)
    else{if(practiceNo===1&&practiceGuideStep===4)setPracticeGuideLayers(value=>value.includes(layer)?value:[...value,layer]);api.event({eventType:'wind_layer_opened',participantId,trialId:trial?.trialId,eventValue:{layer},clientTimeMs:now})}
  }
  async function submitTrial(){
    if(!trial||!forecastPrediction||!intermediate.trim()||!decision||perceivedUncertainty===undefined||perceivedIntervenability===undefined||flightTimeConfidence===undefined||actionChoiceConfidence===undefined)return setError('请完成全部判断、方案选择和四项主观评价')
    if(submissionInFlight.current)return
    submissionInFlight.current=true
    setBusy(true);setFormalSimBusy(true);setError('')
    const submittedAt=performance.now(),decisionTimeMs=Math.max(0,submittedAt-trialStarted.current),finalLayerDwellMs=freezeLayerTracking(submittedAt)
    try{
      const result=await api.submitTrial(participantId,trial.trialId,{
        trialId:trial.trialId,defaultPrediction:forecastPrediction,predictionRtMs:predictionAnsweredAt.current-trialStarted.current,
        intermediateAnswer:intermediate.trim(),intermediateRtMs:intermediateAnsweredAt.current-trialStarted.current,
        finalAction:decision,decisionRtMs:decisionTimeMs,
        perceivedUncertainty,perceivedIntervenability,flightTimeConfidence,actionChoiceConfidence,
        ratingRtMs:submittedAt-(ratingStarted.current||trialStarted.current),layerDwellMs:finalLayerDwellMs,layerSwitchCount:layerSwitchCount.current,layerSequence:layerSequence.current,firstFutureLayerOpenedMs:firstFutureLayerOpenedAt.current===undefined?null:Math.max(0,firstFutureLayerOpenedAt.current-trialStarted.current),predictionChangeCount:predictionChangeCount.current,intermediateChangeCount:intermediateChangeCount.current,decisionChangeCount:decisionChangeCount.current,trialStartedAt:new Date(Date.now()-decisionTimeMs).toISOString(),timedOut:answerTimedOut,
      })
      decisionCommittedRef.current=true;setDecisionCommitted(true);setFormalSimulation(result);setFormalTime(result.frames[0]?.time??0);setDisplayLayer(result.frames[0]?.actualWindLayer??0);setFormalPlaying(true)
      api.event({eventType:'ratings_submitted',participantId,trialId:trial.trialId,eventValue:{perceivedUncertainty,perceivedIntervenability,flightTimeConfidence,actionChoiceConfidence},clientTimeMs:performance.now()})
      api.event({eventType:'formal_simulation_started',sessionId:result.sessionId,participantId,trialId:trial.trialId,eventValue:{action:decision,timedOut:answerTimedOut},clientTimeMs:performance.now()})
    }catch(e){setError(e instanceof Error?e.message:'提交失败')}finally{submissionInFlight.current=false;setBusy(false);setFormalSimBusy(false)}
  }
  useEffect(()=>{
    if(phase!=='trial'||!trial){setRemainingSeconds(FORMAL_TRIAL_SECONDS);return}
    if(decisionCommittedRef.current)return
    const deadline=trialStarted.current+FORMAL_TRIAL_SECONDS*1000
    let expired=false
    const tick=()=>{
      const remaining=Math.max(0,Math.ceil((deadline-performance.now())/1000))
      setRemainingSeconds(remaining)
      if(remaining===0&&!expired){
        expired=true;window.clearInterval(interval)
        if(formalTimeoutAllowed(decisionCommittedRef.current,submissionInFlight.current)){
          const frozenDwellMs=freezeLayerTracking(performance.now())
          setAnswerTimedOut(true)
          api.event({eventType:'trial_timed_out',participantId,trialId:trial.trialId,eventValue:{block:trial.block,globalOrder:trial.globalOrder,layerDwellMs:frozenDwellMs,layerSequence:layerSequence.current},clientTimeMs:performance.now()})
        }
      }
    }
    const interval=window.setInterval(tick,1000)
    tick()
    return()=>window.clearInterval(interval)
  },[phase,trial?.trialId,decisionCommitted])
  useEffect(()=>{
    const settleOnPageLeave=()=>{
      if(phase!=='trial'||!trial||decisionCommittedRef.current)return
      const now=performance.now(),dwell=freezeLayerTracking(now)
      api.event({eventType:'trial_page_left',participantId,trialId:trial.trialId,eventValue:{layerDwellMs:dwell,layerSequence:layerSequence.current},clientTimeMs:now})
    }
    window.addEventListener('pagehide',settleOnPageLeave)
    return()=>window.removeEventListener('pagehide',settleOnPageLeave)
  },[phase,participantId,trial?.trialId])
  async function continueAfterResult(){
    if(!trial||phase!=='trial')return
    api.event({eventType:'trial_completed',participantId,trialId:trial.trialId,eventValue:{block:trial.block,globalOrder:trial.globalOrder,timedOut:answerTimedOut},clientTimeMs:performance.now()})
    if(trial.positionInBlock===6){setBlockRatings({});setPhase('block')}else await loadNext()
  }
  async function submitBlock(){
    if(!trial||Object.values(blockRatings).some(value=>value===undefined))return setError('请完成本组全部评价')
    await api.blockRating(participantId,trial.block,blockRatings as Record<string,number>);setBlockRatings({});await loadNext()
  }
  async function finish(){
    if(!questionnaire.major||!questionnaire.uavExperience||!questionnaire.aviationCourse||!questionnaire.systemManagementExperience||!questionnaire.gameExperience||questionnaire.mapAbility===undefined||questionnaire.sustainedAttention===undefined||questionnaire.overallEffort===undefined||questionnaire.scenarioComprehension===undefined||questionnaire.windRouteReliance===undefined||questionnaire.windRelationImportance===undefined||questionnaire.mentalSimulationUse===undefined||questionnaire.perceivedRealism===undefined||questionnaire.uncertaintyAnalysisEffort===undefined||questionnaire.intervenabilityComparisonEffort===undefined||questionnaire.simulationTriggerSelections.length===0||questionnaire.decisionInformationSelections.length===0)return setError('请完成全部必填背景信息、总体评价和多选题')
    if(questionnaire.simulationTriggerSelections.length>3||questionnaire.decisionInformationSelections.length>4)return setError('第一道多选题最多选择3项，第二道最多选择4项')
    if((questionnaire.simulationTriggerSelections.includes(OTHER_SELECTION)&&!questionnaire.simulationTriggerOther.trim())||(questionnaire.decisionInformationSelections.includes(OTHER_SELECTION)&&!questionnaire.decisionInformationOther.trim()))return setError('选择“其他，请说明”后请填写具体内容')
    setBusy(true);try{await api.complete(participantId,questionnaire);setPhase('complete')}catch(e){setError(e instanceof Error?e.message:'提交失败')}finally{setBusy(false)}
  }

  if(phase!=='trial'&&phase!=='practice')return <main className="experiment-shell"><section className={`page-card${phase==='instructions'||phase==='practice-intro'||phase==='formal-intro'?' instructions-card':''}${phase==='questionnaire'?' questionnaire-card':''}`}>
    {error&&<div className="error">{error}</div>}
    {phase==='start'&&<><h1 className="start-title">UAV 监督决策实验</h1><p>请输入匿名实验编号。若使用已有且未完成的编号，系统会自动从上次已保存的进度继续。</p><input value={participantId} onChange={e=>setParticipantId(e.target.value)} placeholder="例如 P001"/><button className="primary" disabled={busy} onClick={begin}>开始 / 继续实验</button></>}
    {phase==='instructions'&&<TaskInstructions onContinue={()=>setPhase('practice-intro')}/>}
    {phase==='practice-intro'&&<PracticeTaskInstructions busy={busy} onContinue={()=>loadPractice(1)}/>}
    {phase==='comprehension'&&<><h1>理解测试</h1><p>正式任务开始前，请完成以下理解确认。每题请选择一个最符合任务说明的选项。</p>{COMPREHENSION_QUESTIONS.map(q=><fieldset key={q.id}><legend>{q.text}</legend>{q.options.map(o=><label key={o[0]}><input type="radio" name={q.id} checked={answers[q.id]===o[0]} onChange={()=>setAnswers(v=>({...v,[q.id]:o[0]}))}/><strong>{o[0]}.</strong> {o[1]}</label>)}</fieldset>)}{message&&<div className="comprehension-feedback" role="alert" aria-live="polite">{message.split('\n').map((line,index)=><p key={index}>{line}</p>)}</div>}<button className="primary" onClick={checkComprehension}>提交理解测试</button></>}
    {phase==='formal-intro'&&<FormalTaskInstructions busy={busy} onContinue={()=>loadNext()}/>}
    {phase==='block'&&<><h1>第 {trial?.block} 组任务评估</h1><p>请根据刚完成的这一组任务进行评价。</p><Rating label="在完成刚才一组任务的过程中，您投入了多少的心理努力？" lowLabel="非常低" midLabel="中等" highLabel="非常高" value={blockRatings.mentalEffort} set={v=>setBlockRatings(x=>({...x,mentalEffort:v}))}/><Rating label="完成刚才一组任务后，您当前的主观疲劳程度如何？" lowLabel="完全不疲劳" midLabel="中等疲劳" highLabel="极度疲劳" value={blockRatings.fatigue} set={v=>setBlockRatings(x=>({...x,fatigue:v}))}/><Rating label="总体而言，您认为刚才一组任务的难度如何？" lowLabel="非常容易" midLabel="中等" highLabel="非常困难" value={blockRatings.taskDifficulty} set={v=>setBlockRatings(x=>({...x,taskDifficulty:v}))}/><Rating label="在刚才这一组任务中，我会在头脑中逐步推演无人机经过不同时间层和风区的过程。" value={blockRatings.mentalSimulationStrategy} set={v=>setBlockRatings(x=>({...x,mentalSimulationStrategy:v}))}/><Rating label="在刚才这一组任务中，我会比较至少两种调度方案可能产生的结果。" value={blockRatings.multiActionComparisonStrategy} set={v=>setBlockRatings(x=>({...x,multiActionComparisonStrategy:v}))}/><Rating label="在刚才这一组任务中，我主要依据一个关键线索做出选择。" value={blockRatings.simpleCueStrategy} set={v=>setBlockRatings(x=>({...x,simpleCueStrategy:v}))}/><button className="primary" disabled={Object.values(blockRatings).some(value=>value===undefined)} onClick={submitBlock}>继续</button></>}
    {phase==='questionnaire'&&<>
      <h1>结束问卷</h1>
      <section className="questionnaire-section" aria-labelledby="questionnaire-background">
        <div className="questionnaire-section-heading"><span>第一部分</span><h2 id="questionnaire-background">专业领域与相关经验</h2></div>
        <label>1. 您的专业领域最接近以下哪一类？<select value={questionnaire.major} onChange={event=>setQuestionnaire(value=>({...value,major:event.target.value as ProfessionalField}))}><option value="" disabled>请选择</option>{PROFESSIONAL_FIELDS.map(field=><option key={field} value={field}>{field}</option>)}</select></label>
        <ExperienceSelect label="2. 无人机相关经验" value={questionnaire.uavExperience} optionLabels={{none:'无：从未实际操控',some:'有一些：偶尔体验或少量操控',extensive:'较丰富：经常操控、接受过训练或持有相关资质'}} set={uavExperience=>setQuestionnaire(v=>({...v,uavExperience}))}/>
        <ExperienceSelect label="3. 航空、交通或控制相关学习或工作经验" value={questionnaire.aviationCourse} set={aviationCourse=>setQuestionnaire(v=>({...v,aviationCourse}))}/>
        <ExperienceSelect label="4. 您是否有需要持续监控系统状态并进行调度或干预的学习、训练或工作经验？例如生产监控、交通调度、设备运维或控制室操作。" value={questionnaire.systemManagementExperience} set={systemManagementExperience=>setQuestionnaire(v=>({...v,systemManagementExperience}))}/>
        <ExperienceSelect label="5. 飞行或策略类游戏经验" value={questionnaire.gameExperience} set={gameExperience=>setQuestionnaire(v=>({...v,gameExperience}))}/>
      </section>
      <section className="questionnaire-section" aria-labelledby="questionnaire-overall">
        <div className="questionnaire-section-heading"><span>第二部分</span><h2 id="questionnaire-overall">总体评价</h2></div>
        <Rating label="6. 本次任务需要我持续集中注意力。" value={questionnaire.sustainedAttention} set={value=>setQuestionnaire(v=>({...v,sustainedAttention:value}))}/>
        <Rating label="7. 本次任务让我感到较强的时间压力。" value={questionnaire.overallEffort} set={value=>setQuestionnaire(v=>({...v,overallEffort:value}))}/>
        <Rating label="8. 整体而言，我能够理解本实验中的任务规则和作答要求。" value={questionnaire.scenarioComprehension} set={value=>setQuestionnaire(v=>({...v,scenarioComprehension:value}))}/>
        <Rating label="9. 我能够根据地图和路线图较快判断位置、方向和路线之间的关系。" value={questionnaire.mapAbility} set={value=>setQuestionnaire(v=>({...v,mapAbility:value}))}/>
        <Rating label="10. 做出判断时，我会结合风场信息与无人机路线之间的相对关系。" value={questionnaire.windRouteReliance} set={value=>setQuestionnaire(v=>({...v,windRouteReliance:value}))}/>
        <Rating label="11. 风场与路线之间的相对关系对我的判断有多重要？" lowLabel="完全不重要" midLabel="一般" highLabel="非常重要" value={questionnaire.windRelationImportance} set={value=>setQuestionnaire(v=>({...v,windRelationImportance:value}))}/>
        <Rating label="12. 作答时，我会在头脑中想象无人机沿路线继续飞行时可能发生的情况。" value={questionnaire.mentalSimulationUse} set={value=>setQuestionnaire(v=>({...v,mentalSimulationUse:value}))}/>
        <Rating label="13. 就我的理解而言，该实验场景能够反映无人机配送监管任务的一些基本特征。" value={questionnaire.perceivedRealism} set={value=>setQuestionnaire(v=>({...v,perceivedRealism:value}))}/>
        <Rating label="14. 当未来风场更加不确定时，我会投入更多时间分析和推演可能的结果。" value={questionnaire.uncertaintyAnalysisEffort} set={value=>setQuestionnaire(v=>({...v,uncertaintyAnalysisEffort:value}))}/>
        <Rating label="15. 当不同调度方案可能造成较大的飞行时间差异时，我会更仔细地比较多个方案。" value={questionnaire.intervenabilityComparisonEffort} set={value=>setQuestionnaire(v=>({...v,intervenabilityComparisonEffort:value}))}/>
        <MultiSelectQuestion question="16. 在以下哪些情况下，你通常会更仔细地推演无人机的飞行过程，或比较多个调度方案？" maxSelections={3} options={SIMULATION_TRIGGER_OPTIONS} value={questionnaire.simulationTriggerSelections} set={simulationTriggerSelections=>setQuestionnaire(v=>({...v,simulationTriggerSelections}))} otherValue={questionnaire.simulationTriggerOther} setOther={simulationTriggerOther=>setQuestionnaire(v=>({...v,simulationTriggerOther}))}/>
        <MultiSelectQuestion question="17. 在判断飞行时间和选择调度方案时，你主要使用了哪些信息或方法？" maxSelections={4} options={DECISION_INFORMATION_OPTIONS} value={questionnaire.decisionInformationSelections} set={decisionInformationSelections=>setQuestionnaire(v=>({...v,decisionInformationSelections}))} otherValue={questionnaire.decisionInformationOther} setOther={decisionInformationOther=>setQuestionnaire(v=>({...v,decisionInformationOther}))}/>
        <label className="questionnaire-open-ended">18. 为了更准确地判断飞行时间和选择调度方案，你认为平台还需要提供哪些信息？（选填）<textarea maxLength={2000} value={questionnaire.additionalInformation} onChange={e=>setQuestionnaire(v=>({...v,additionalInformation:e.target.value}))}/></label>
      </section>
      <button className="primary" disabled={busy} onClick={finish}>提交并完成</button>
    </>}
    {phase==='complete'&&<><h1>实验完成</h1><p>感谢参与。请通知实验员，数据已经保存。</p></>}
  </section></main>

  if(!trial)return <main className="center">正在载入实验任务…</main>
  const canvasFrame=phase==='practice'?practiceFrame:formalFrame
  const canvasSimulation=phase==='practice'?practiceSimulation:formalSimulation
  const canvasTrial=phase==='trial'&&decisionCommitted&&formalSimulation?{...trial,windLayers:formalSimulation.actualWindLayers}:trial
  const finalWindVisible=phase==='trial'&&decisionCommitted&&!!formalSimulation
  const answersComplete=!!forecastPrediction&&!!intermediate&&!!decision&&perceivedUncertainty!==undefined&&perceivedIntervenability!==undefined&&flightTimeConfidence!==undefined&&actionChoiceConfidence!==undefined
  const guidedPractice=phase==='practice'&&practiceNo===1&&practiceGuideStep>0
  return <main><div className="trial-progress"><span>{phase==='practice'?`练习 ${practiceNo}/4`:`第 ${trial.block}/3 组 · 第 ${trial.positionInBlock}/6 个任务`}</span>{phase==='trial'&&!decisionCommitted&&<span className={`trial-countdown${answerTimedOut?' expired':''}`} role="timer" aria-label={`本题剩余时间 ${formatCountdown(remainingSeconds)}`}><small>{answerTimedOut?'作答时间已到：地图已冻结，请完成必答项并提交':'全部答案提交剩余时间'}</small><strong>{formatCountdown(remainingSeconds)}</strong></span>}{phase==='trial'&&decisionCommitted&&<span className="trial-countdown result" role="timer"><small>仿真与结果比较剩余时间</small><strong>{resultSeconds} 秒</strong></span>}</div>{error&&<div className="error">{error}</div>}{guidedPractice&&practiceGuideStep<8&&<PracticeGuide step={practiceGuideStep} openedLayers={practiceGuideLayers} simulationState={practiceGuideSimulation} mapInteracted={practiceGuideMapInteracted} windInspected={practiceGuideWindInspected} hasInitialDecision={!!decision} feedbackVisible={!!practiceFeedback} onNext={()=>setPracticeGuideStep(step=>Math.min(8,step+1))}/>}<section className={`experiment-workspace${guidedPractice&&practiceGuideStep<8?' guided-practice':''}`}>
    <section className={`map-panel${phase==='trial'&&answerTimedOut&&!decisionCommitted?' frozen-map':''}`}><div className="map-head"><div data-guide-target="goal" className={`map-head-goal${guidedPractice&&practiceGuideStep===1?' guide-highlight':''}`}><span>调度目标</span><strong>{TASK_GOAL}</strong><small>{TASK_GOAL_NOTE}</small></div><div data-guide-target="layers" className={`layers${guidedPractice&&practiceGuideStep===4?' guide-highlight guide-highlight-local':''}`}>{[0,1,2].map(l=><button key={l} disabled={phase==='trial'&&(decisionCommitted||answerTimedOut)} className={displayLayer===l?'active':''} onClick={()=>selectLayer(l)}>T{l}<small>{l===0?'场景第 0 秒':`场景第 ${l*120} 秒`}</small></button>)}</div></div>{phase==='practice'&&<div data-guide-target="simulator" className={`practice-simulator${guidedPractice&&practiceGuideStep===7?' guide-highlight':''}`}><span>选择未来仿真方案：</span>{ACTIONS.map(action=><button type="button" key={action.id} disabled={practiceSimBusy||(guidedPractice&&practiceGuideStep<7)} className={practiceSimulation?.action===action.id?'active':''} onClick={()=>playPractice(action.id)}>▶ {action.label}</button>)}{practiceSimulation&&<><button type="button" onClick={togglePracticePlayback}>{practicePlaying?'暂停':'继续'}</button><button type="button" onClick={()=>{setPracticeTime(practiceSimulation.frames[0]?.time??0);setDisplayLayer(practiceSimulation.frames[0]?.actualWindLayer??0);setPracticePlaying(true)}}>重新仿真</button></>}</div>}{phase==='trial'&&decisionCommitted&&formalSimulation&&<div className="formal-simulator"><span><strong>最终风场仿真</strong> · {ACTIONS.find(item=>item.id===formalSimulation.action)?.label}</span><progress aria-label="最终风场仿真进度" max={formalSimulation.frames.at(-1)?.time??1} value={formalTime}/></div>}<div data-guide-target="map" className={`guide-map-target${guidedPractice&&[3,5].includes(practiceGuideStep)?' guide-highlight':''}`}><SimulationCanvas interactionDisabled={phase==='trial'&&(decisionCommitted||answerTimedOut)} onMapInteract={()=>{if(guidedPractice&&practiceGuideStep===3)setPracticeGuideMapInteracted(true)}} onWindInspect={()=>{if(guidedPractice&&practiceGuideStep===5)setPracticeGuideWindInspected(true)}} trial={canvasTrial} frame={canvasFrame} displayLayer={displayLayer} trail={[]} activePathId={canvasSimulation?.pathId} flightTimeSeconds={canvasFrame&&canvasSimulation?practiceFlightTime(canvasFrame.time,canvasSimulation.action,trial.delaySeconds):undefined}/></div><div className="legend"><span><i className="a"/>Path-A</span><span><i className="b"/>Path-B</span><span><i className="u"/>无人机</span><span className="legend-divider"/><strong>{finalWindVisible?'最终平均风速':'预测平均风速'}</strong><span><i className="wind-low"/>绿色：低（&lt;4 m/s）</span><span><i className="wind-medium"/>黄色：中（4–&lt;7 m/s）</span><span><i className="wind-high"/>红色：高（≥7 m/s）</span><span><i className="wind-arrow">→</i>风吹向</span>{!finalWindVisible&&<span><i className="wind-range"/>方向可能范围</span>}<span>W1/W2 为风区编号</span></div></section>
    <aside className="response-panel">
      <h2>{phase==='practice'?'练习任务':decisionCommitted?'仿真与结果比较':'完成本任务'}</h2>
      {guidedPractice&&practiceGuideStep===8&&<div className="practice-guide-complete"><strong>任务引导已完成</strong><p>接下来请你认真观察任务场景并使用仿真功能，并提交你认为飞行时间最短的方案。</p></div>}
      <div data-guide-target="benchmark" className={guidedPractice&&practiceGuideStep===6?'guide-highlight':''}><NoWindBenchmarks trial={trial}/></div>
      <section data-guide-target="answers"><h3>1. {trial.defaultPredictionQuestion}</h3>{trial.defaultPredictionOptions.map(option=><button key={option} disabled={busy||(phase==='practice'&&(!!practiceFeedback||(guidedPractice&&practiceGuideStep!==8)))||(phase==='trial'&&decisionCommitted)} className={forecastPrediction===option?'selected':''} onClick={()=>selectForecastPrediction(option)}>{option}</button>)}{phase==='trial'&&<Rating disabled={busy||decisionCommitted} label="我对本轮无人机飞行时间的判断有信心。" value={flightTimeConfidence} set={value=>selectRating('flightConfidence',value)}/>}</section>
      {phase==='practice'?<><section data-guide-target="actions" className={guidedPractice&&practiceGuideStep===2?'guide-highlight':''}><h3>2. 选择预计飞行时间最短的调度方案</h3>{ACTIONS.map(a=><button key={a.id} disabled={busy||!!practiceFeedback||(guidedPractice&&practiceGuideStep!==8)} className={decision===a.id?'selected':''} onClick={()=>selectDecision(a.id)}>{a.label}<small>{a.detail}</small></button>)}</section>{practiceFeedback?<div data-guide-target="feedback" className="practice-feedback">{practiceFeedback.split('\n\n').map((paragraph,index)=><p key={index}>{paragraph}</p>)}<button className="primary" onClick={continuePractice}>{practiceNo<4?'进入下一练习':'进入理解测试'}</button></div>:<button className="primary" disabled={busy||!forecastPrediction||!decision||(guidedPractice&&practiceGuideStep!==8)} onClick={submitPractice}>{busy?'正在提交…':guidedPractice?'提交练习答案并查看反馈':'提交练习'}</button>}</>
      :<>
        <section><h3>2. {trial.intermediateQuestion}</h3>{trial.intermediateOptions.map(option=><button key={option} disabled={busy||decisionCommitted} className={intermediate===option?'selected':''} onClick={()=>selectIntermediate(option)}>{option}</button>)}<Rating disabled={busy||decisionCommitted} label="根据本轮提供的风场预测信息，你认为焦点无人机最终飞行时间可能出现的变化范围有多大？" lowLabel="非常小" midLabel="中等" highLabel="非常大" value={perceivedUncertainty} set={value=>selectRating('uncertainty',value)}/></section>
        <section><h3>3. 选择预计飞行时间最短的调度方案</h3>{ACTIONS.map(a=><button key={a.id} disabled={busy||decisionCommitted} className={decision===a.id?'selected':''} onClick={()=>selectDecision(a.id)}>{a.label}<small>{a.detail}</small></button>)}<Rating disabled={busy||decisionCommitted} label="在本轮场景中，你认为选择不同调度方案会使焦点无人机的飞行时间产生多大差异？" lowLabel="非常小" midLabel="中等" highLabel="非常大" value={perceivedIntervenability} set={value=>selectRating('intervenability',value)}/><Rating disabled={busy||decisionCommitted} label="我对本轮选择的调度方案有信心。" value={actionChoiceConfidence} set={value=>selectRating('actionConfidence',value)}/></section>
        {!decisionCommitted&&answerTimedOut&&<div className="simulation-observation-note">90 秒作答阶段已结束。地图和预测时间层已冻结；请完成上方所有必答项后主动提交，系统不会自动跳题。</div>}
        {!decisionCommitted&&<button className="primary" disabled={busy||formalSimBusy||!answersComplete} onClick={submitTrial}>{formalSimBusy||busy?'正在保存并载入仿真…':'提交本轮答案'}</button>}
        {decisionCommitted&&formalSimulation&&<><ActionOutcomeTimes simulation={formalSimulation}/><div className="simulation-observation-note">答案已锁定。结果最多展示30秒；你也可以提前进入下一轮。</div><button className="primary" onClick={continueAfterResult}>{trial.positionInBlock===6?'进入本组评估':'进入下一轮'}</button></>}
      </>}
    </aside>
  </section></main>
}

export function TaskInstructions({onContinue}:{onContinue:()=>void}){
  return <>
    <h1>任务说明</h1>
    <p>你将扮演一名城市无人机配送中心的运行调度员。</p>
    <p>每轮任务中，一架无人机正在等待放行。你的目标是结合当前及未来的风场信息，从以下三种方案中选择无人机预计飞行时间最短的方案：</p>
    <ol className="instruction-list action-list">
      <li>立即沿 Path-A 起飞；</li>
      <li>等待120秒后沿 Path-A 起飞；</li>
      <li>立即沿 Path-B 起飞。</li>
    </ol>
    <figure className="task-interface-preview">
      <img src="/assets/task-interface-guide.png" alt="任务界面示意图，展示时间层、候选航路、风区、风向和无人机位置" loading="eager" fetchPriority="high"/>
    </figure>

    <h2>你需要查看的信息</h2>
    <ul className="instruction-list">
      <li>T0、T1和T2分别表示当前及未来不同时间层的风场；</li>
      <li>风区颜色表示风速等级；</li>
      <li>实线箭头表示平均风向，浅色扇形表示风向可能出现的范围；</li>
      <li>将鼠标移到风区或箭头上，可以查看更详细的风场信息；</li>
      <li>蓝色Path-A和橙色Path-B是候选航路；</li>
    </ul>

    <h2>风场如何影响飞行时间</h2>
    <p>风向和风速会影响无人机沿航路方向的前进速度，从而改变飞行时间：</p>
    <ul className="instruction-list">
      <li>顺风通常会提高无人机的前进速度，使飞行时间缩短；</li>
      <li>逆风通常会降低无人机的前进速度，使飞行时间延长；</li>
      <li>侧风会增加无人机保持航路的难度，也可能延长飞行时间；</li>
      <li>风速越大，风场对飞行时间的影响通常越明显。</li>
    </ul>

    <h2>需要注意的事项</h2>
    <ol className="instruction-list">
      <li>无人机在飞行过程中可能经过不同的风区，也可能跨越不同的风场时间层。因此，不能只看起飞时刻的风场，还需要结合航路和飞行过程查看相关时间层。</li>
      <li>你不需要手动计算无人机的具体速度，只需要判断不同航段主要受到顺风、逆风还是侧风影响，并据此比较三种方案的预计飞行时间。</li>
      <li>“等待120秒后沿Path-A起飞”方案中的120秒地面等待时间不计入飞行时间。三个方案比较的都是无人机从起飞到抵达目的地所需的时间。</li>
    </ol>
    <button className="primary" onClick={onContinue}>继续</button>
  </>
}

export function PracticeTaskInstructions({busy,onContinue}:{busy:boolean;onContinue:()=>void}){
  return <>
    <span className="stage-kicker">场景说明已完成</span>
    <h1>练习任务说明</h1>

    <h2>练习任务</h2>
    <p>正式实验开始前，你将完成 <strong>4 个带反馈的练习任务</strong>，以熟悉预测信息、无风基准、方案选择和未来仿真的操作方式。</p>
    <p>在练习任务中，你需要：</p>
    <ol className="formal-steps" aria-label="练习任务流程">
      <li>查看任务场景、预测信息和当前场景的无风基准；</li>
      <li>选择一个调度方案并运行未来仿真。仿真过程中可以暂停、继续、重新播放，也可以切换并查看其他方案；</li>
      <li>综合预测信息和仿真结果，判断各方案的飞行时间，并选择你认为飞行时间最短的调度方案；</li>
      <li>提交答案，查看正确答案及三种方案的最终飞行时间；</li>
      <li>点击“进入下一练习”，继续完成后续任务。</li>
    </ol>
    <p>完成全部练习任务后，你还需要通过一项理解测试。通过测试后即可进入正式实验。</p>

    <h2>仿真计时说明</h2>
    <p>本实验的仿真动画均以 <strong>10 倍速度</strong>播放，地图右上角的计时器显示的是无人机的<strong>实际飞行时间</strong>。</p>
    <p>为了减少实验的等待时间，如果选择“延迟120秒起飞”的方案，系统会直接从场景时间第120秒的起飞状态开始播放，不展示无人机在地面等待的过程。此时，风场和其他环境状态也会同步到场景时间第120秒。</p>
    <button className="primary" disabled={busy} onClick={onContinue}>开始练习任务</button>
  </>
}

export function FormalTaskInstructions({busy,onContinue}:{busy:boolean;onContinue:()=>void}){
  return <>
    <h1>正式实验</h1>
    <p>正式实验共3组，每组6轮，共18轮。每轮任务相互独立。每轮任务流程分成两个阶段：</p>
    <h3>1. 作答阶段：90秒</h3>
    <ol className="formal-steps">
      <li>查看无风基准以及T0、T1和T2的预测风场；</li>
      <li>比较三种调度方案；</li>
      <li>完成方案选择和其他必答题；</li>
      <li>点击“提交本轮答案”。</li>
    </ol>
    <p><strong>正式实验的作答阶段不能运行仿真。请根据预测风场、航路和无风基准直接作出判断。</strong></p>
    <p>页面顶部的倒计时只表示剩余作答时间，不会推动场景中的风场时间。</p>
    <p>提交前可以修改答案；提交后，本轮答案将被锁定，不能再更改。</p>
    <p>如果90秒结束时仍未提交，风场查看功能将被冻结。已经填写的答案会保留，但系统不会自动提交，请尽快完成必答项并主动点击“提交本轮答案”。</p>
    <h3>2. 结果呈现阶段：最多30秒</h3>
    <p>提交后，本轮答案将被锁定，不能再修改。系统将播放你所选方案的实际运行过程，并呈现三种方案在本轮实际风况下的飞行时间。</p>
    <p>页面顶部会显示结果查看剩余时间。你可以在倒计时结束前点击“进入下一轮”；若未操作，倒计时结束后系统将自动进入下一轮。</p>
    <p>确认已经理解后，请点击“开始正式实验”。</p>
    <button className="primary" disabled={busy} onClick={onContinue}>开始正式实验</button>
  </>
}

export function NoWindBenchmarks({trial}:{trial:AssignedTrial}){const routes=[{pathId:'Path-A',benchmark:trial.noWindBenchmarks.release},{pathId:'Path-B',benchmark:trial.noWindBenchmarks.reroute}];return <div className="action-baselines benchmark-table" aria-label="无风基准"><div className="baseline-heading"><strong>无风基准</strong><span>按路线提供，不含实际风场结果</span></div>{routes.map(({pathId,benchmark})=><div className="benchmark-action" key={pathId}><b>{pathId}</b><span>无风飞行总时间 {Math.round(benchmark.flightTimeSeconds)} 秒</span>{benchmark.zoneIntervals.map(zone=><small key={zone.zoneId}>{zone.zoneId}：{zone.entryTimeSeconds===null?'无风航迹不经过':`起飞后第 ${Math.round(zone.entryTimeSeconds)} 秒进入，第 ${Math.round(zone.exitTimeSeconds!)} 秒离开`}</small>)}</div>)}</div>}
function ActionOutcomeTimes({simulation}:{simulation:FormalSimulationResponse}){return <div className="action-outcome-times" aria-label="最终风场下三种方案的飞行时间"><div className="outcome-heading"><strong>最终风场下的飞行时间</strong><span>地面等待不计入</span></div>{ACTIONS.map(action=><div key={action.id} className={simulation.action===action.id?'selected':''}><span>{action.label}{simulation.action===action.id&&<small>已提交方案</small>}</span><b>{simulation.actionTimes[action.id].toFixed(1)} 秒</b></div>)}</div>}
function Rating({label,value,set,lowLabel='完全不同意',midLabel='既不同意也不反对',highLabel='完全同意',disabled=false}:{label:string;value:number|undefined;set:(value:number)=>void;lowLabel?:string;midLabel?:string;highLabel?:string;disabled?:boolean}){return <div className="rating"><span>{label}</span><div>{[1,2,3,4,5,6,7].map(n=><button type="button" key={n} disabled={disabled} className={value===n?'active':''} onClick={()=>set(n)}>{n}</button>)}</div><small>1={lowLabel}　　4={midLabel}　　7={highLabel}</small></div>}
type ExperienceLevel=''|'none'|'some'|'extensive'
type ExperienceOptionLabels={none:string;some:string;extensive:string}
const DEFAULT_EXPERIENCE_OPTION_LABELS:ExperienceOptionLabels={none:'无相关经验',some:'有一点相关经验',extensive:'有丰富的相关经验'}
export function ExperienceSelect({label,value,set,optionLabels=DEFAULT_EXPERIENCE_OPTION_LABELS}:{label:string;value:ExperienceLevel;set:(value:ExperienceLevel)=>void;optionLabels?:ExperienceOptionLabels}){return <label>{label}<select value={value} onChange={event=>set(event.target.value as ExperienceLevel)}><option value="" disabled>请选择</option><option value="none">{optionLabels.none}</option><option value="some">{optionLabels.some}</option><option value="extensive">{optionLabels.extensive}</option></select></label>}
export function MultiSelectQuestion({question,maxSelections,options,value,set,otherValue,setOther}:{question:string;maxSelections:number;options:readonly string[];value:string[];set:(value:string[])=>void;otherValue:string;setOther:(value:string)=>void}){return <div className="multi-select-question" role="group" aria-label={question}><div className="multi-select-question-title">{question}</div><strong>最多选择{maxSelections}项。</strong>{options.map(option=>{const checked=value.includes(option),atLimit=value.length>=maxSelections;return <label key={option}><input type="checkbox" checked={checked} disabled={!checked&&atLimit} onChange={()=>set(checked?value.filter(item=>item!==option):[...value,option])}/>{option}{option===OTHER_SELECTION&&checked&&<input aria-label={`${question}其他说明`} maxLength={500} value={otherValue} onChange={event=>setOther(event.target.value)} placeholder="请说明"/>}</label>})}</div>}
