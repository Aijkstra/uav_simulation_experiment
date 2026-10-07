import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import App, { COMPREHENSION_QUESTIONS, ExperienceSelect, FORMAL_TRIAL_SECONDS, MultiSelectQuestion, PracticeGuide, RESULT_REVIEW_SECONDS, FormalTaskInstructions, NoWindBenchmarks, PracticeTaskInstructions, TASK_GOAL, TASK_GOAL_NOTE, TaskInstructions, comprehensionFeedback, formalTimeoutAllowed, formatCountdown, practiceFlightTime, responseOptionsDisabled, simulationFrameForPhase } from './App'
import { routeRelativeWindAngle, windDirectionBounds } from './SimulationCanvas'
import type { AssignedTrial, Frame, NoWindBenchmark, Route, WindZone } from './types'

describe('experiment entry page', () => {
  it('shows anonymous id and the task entry controls', () => {
    const html = renderToStaticMarkup(<App />)
    expect(html).toContain('匿名实验编号')
    expect(html).toContain('开始 / 继续实验')
    expect(html).not.toContain('2700–3300秒')
    expect(html).not.toContain('FUTURE-STATE DECISION TASK')
    expect(html).not.toContain('本地实验系统')
    expect(html).not.toContain('可预测性 高')
    expect(html).not.toContain('可干预性 高')
    expect(html).not.toContain('plannedBestAction')
  })
})

describe('questionnaire experience options', () => {
  it('uses operationally defined labels for UAV experience', () => {
    const html=renderToStaticMarkup(<ExperienceSelect label="无人机相关经验" value="" set={()=>undefined} optionLabels={{none:'无：从未实际操控',some:'有一些：偶尔体验或少量操控',extensive:'较丰富：经常操控、接受过训练或持有相关资质'}}/>)
    expect(html).toContain('无：从未实际操控')
    expect(html).toContain('有一些：偶尔体验或少量操控')
    expect(html).toContain('较丰富：经常操控、接受过训练或持有相关资质')
  })
})

describe('questionnaire multi-select questions', () => {
  it('shows the selection limit and an explanation field for selected other', () => {
    const html=renderToStaticMarkup(<MultiSelectQuestion question="测试题" maxSelections={3} options={['选项一','其他，请说明']} value={['其他，请说明']} set={()=>undefined} otherValue="补充说明" setOther={()=>undefined}/>)
    expect(html).toContain('最多选择3项')
    expect(html).toContain('其他，请说明')
    expect(html).toContain('补充说明')
    expect(html).toContain('class="multi-select-question"')
    expect(html).not.toContain('<fieldset')
  })
})

describe('wind direction uncertainty', () => {
  it('returns the minimum and maximum direction around north', () => {
    expect(windDirectionBounds(350,25)).toEqual([325,15])
  })

  it('expresses wind direction relative to the affected route', () => {
    const route:Route={pathId:'Path-A',taskRole:'focused_candidate',waypoints:[
      {waypoint_id:'A',x:0,y:0},{waypoint_id:'B',x:100,y:0},
    ]}
    const zone:WindZone={zoneId:'W1',cx:50,cy:0,radius:80,meanSpeed:5,directionDeg:0,speedSD:0,directionSD:0,isForecast:false}
    expect(routeRelativeWindAngle(route,zone)).toBeCloseTo(0)
    expect(routeRelativeWindAngle(route,{...zone,directionDeg:90})).toBeCloseTo(90)
    expect(routeRelativeWindAngle(route,{...zone,directionDeg:180})).toBeCloseTo(180)
  })
})

describe('editable trial responses', () => {
  it('keeps every formal-trial answer editable until submission', () => {
    expect(responseOptionsDisabled('trial', '上一练习的反馈')).toBe(false)
  })

  it('locks practice answers only after feedback or while submitting', () => {
    expect(responseOptionsDisabled('practice', '已经提交')).toBe(true)
    expect(responseOptionsDisabled('practice', '', true)).toBe(true)
  })
})

describe('formal trial timeout arbitration', () => {
  it('does not time out after the participant has committed a decision', () => {
    expect(formalTimeoutAllowed(true,false)).toBe(false)
  })

  it('does not start a second transition while a submission is in flight', () => {
    expect(formalTimeoutAllowed(false,true)).toBe(false)
    expect(formalTimeoutAllowed(false,false)).toBe(true)
  })
})

describe('participant action goal', () => {
  it('defines the dispatch goal as flight time from takeoff to arrival', () => {
    expect(TASK_GOAL).toContain('预计飞行时间最短')
    expect(TASK_GOAL_NOTE).toContain('从起飞到抵达目的地')
    expect(TASK_GOAL_NOTE).toContain('不计入飞行时间')
    expect(TASK_GOAL).not.toContain('暴露')
    expect(TASK_GOAL_NOTE).not.toContain('暴露')
  })
})

describe('route-based no-wind benchmark', () => {
  it('shows Path-A and Path-B once without duplicating release and delay', () => {
    const benchmark=(action:'release'|'delay'|'reroute',pathId:string,start:number):NoWindBenchmark=>({action,pathId,startTimeSeconds:start,flightTimeSeconds:100,arrivalTimeSeconds:start+100,zoneIntervals:[]})
    const trial={noWindBenchmarks:{release:benchmark('release','Path-A',0),delay:benchmark('delay','Path-A',120),reroute:benchmark('reroute','Path-B',0)}} as AssignedTrial
    const html=renderToStaticMarkup(<NoWindBenchmarks trial={trial}/>)
    expect((html.match(/<b>Path-A<\/b>/g)??[]).length).toBe(1)
    expect((html.match(/<b>Path-B<\/b>/g)??[]).length).toBe(1)
    expect(html).not.toContain('<b>立即放行</b>')
    expect(html).not.toContain('<b>延迟起飞</b>')
  })

  it('rounds wind-zone entry and exit times to whole seconds', () => {
    const benchmark=(action:'release'|'delay'|'reroute',pathId:string):NoWindBenchmark=>({action,pathId,startTimeSeconds:0,flightTimeSeconds:100.04,arrivalTimeSeconds:100.04,zoneIntervals:[{zoneId:'W1',entryTimeSeconds:24.49,exitTimeSeconds:68.5}]})
    const trial={noWindBenchmarks:{release:benchmark('release','Path-A'),delay:benchmark('delay','Path-A'),reroute:benchmark('reroute','Path-B')}} as AssignedTrial
    const html=renderToStaticMarkup(<NoWindBenchmarks trial={trial}/>)
    expect(html).toContain('W1：')
    expect(html).toContain('起飞后第 24 秒进入，第 69 秒离开')
    expect(html).toContain('无风飞行总时间 100 秒')
    expect(html).not.toContain('延迟起飞与立即放行均使用 Path-A')
  })
})

describe('task instructions', () => {
  it('keeps the opening page focused on the scenario instructions', () => {
    const html = renderToStaticMarkup(<TaskInstructions onContinue={()=>undefined}/>)
    expect(html).toContain('<h1>任务说明</h1>')
    expect(html).toContain('城市无人机配送中心的运行调度员')
    expect(html).toContain('选择无人机预计飞行时间最短的方案')
    expect(html).toContain('等待120秒后沿 Path-A 起飞')
    expect(html).toContain('风区颜色表示风速等级')
    expect(html).not.toContain('Path-C')
    expect(html).toContain('<h2>风场如何影响飞行时间</h2>')
    expect(html).toContain('顺风通常会提高无人机的前进速度')
    expect(html).toContain('逆风通常会降低无人机的前进速度')
    expect(html).toContain('侧风会增加无人机保持航路的难度')
    expect(html).toContain('你不需要手动计算无人机的具体速度')
    expect(html).toContain('120秒地面等待时间不计入飞行时间')
    expect(html).toContain('<h2>需要注意的事项</h2>')
    expect(html).toContain('/assets/task-interface-guide.png')
    expect(html.indexOf('<figure class="task-interface-preview">')).toBeGreaterThan(html.indexOf('立即沿 Path-B 起飞'))
    expect(html.indexOf('<figure class="task-interface-preview">')).toBeLessThan(html.indexOf('<h2>你需要查看的信息</h2>'))
    expect(html).not.toContain('地速 =')
    expect(html).not.toContain('基础空速')
    expect(html).not.toContain('<h2>运行判断</h2>')
    expect(html).not.toContain('<h2>练习任务</h2>')
    expect(html).not.toContain('<h2>仿真计时</h2>')
    expect(html).not.toContain('正式实验共')
    expect(html).not.toContain('每次正式任务代表一次独立的配送调度')
  })

  it('moves judgment and practice guidance to a separate pre-practice page', () => {
    const html = renderToStaticMarkup(<PracticeTaskInstructions busy={false} onContinue={()=>undefined}/>)
    expect(html).toContain('<h1>练习任务说明</h1>')
    expect(html).not.toContain('<h2>运行判断</h2>')
    expect(html).not.toContain('作为比较航路和判断风场影响的基础')
    expect(html).toContain('<h2>练习任务</h2>')
    expect(html).toContain('正式实验开始前，你将完成 <strong>4 个带反馈的练习任务</strong>')
    expect(html).toContain('仿真过程中可以暂停、继续、重新播放')
    expect(html).not.toContain('拖动进度')
    expect(html).toContain('三种方案的最终飞行时间')
    expect(html).toContain('进入下一练习')
    expect(html).toContain('通过测试后即可进入正式实验')
    expect(html).toContain('<h2>仿真计时说明</h2>')
    expect(html).toContain('<strong>10 倍速度</strong>')
    expect(html).toContain('风场和其他环境状态也会同步到场景时间第120秒')
    expect(html).toContain('开始练习任务')
  })

  it('moves the formal workflow to the pre-formal instruction page', () => {
    const html = renderToStaticMarkup(<FormalTaskInstructions busy={false} onContinue={()=>undefined}/>)
    expect(html).toContain('<h1>正式实验</h1>')
    expect(html).toContain('正式实验共3组，每组6轮，共18轮')
    expect(html).toContain('每轮任务相互独立')
    expect(html).toContain('每轮任务流程分成两个阶段：')
    expect(html).not.toContain('<h2>每轮任务流程</h2>')
    expect(html).toContain('<h3>1. 作答阶段：90秒</h3>')
    expect(html).toContain('查看无风基准以及T0、T1和T2的预测风场')
    expect(html).toContain('正式实验的作答阶段不能运行仿真')
    expect(html).toContain('提交前可以修改答案')
    expect(html).toContain('本轮答案将被锁定')
    expect(html).toContain('<h3>2. 结果呈现阶段：最多30秒</h3>')
    expect(html).toContain('不会推动场景中的风场时间')
    expect(html).toContain('风场查看功能将被冻结')
    expect(html).toContain('系统不会自动提交')
    expect(html).toContain('倒计时结束后系统将自动进入下一轮')
    expect(html).toContain('确认已经理解后，请点击“开始正式实验”')
    expect(html).not.toContain('仿真计时')
    expect(html).not.toContain('地图右上角的计时器显示无人机的实际飞行时间')
  })

  it('counts airborne time without including a delayed departure', () => {
    expect(practiceFlightTime(188.9, 'release', 120)).toBe(188.9)
    expect(practiceFlightTime(90, 'delay', 120)).toBe(0)
    expect(practiceFlightTime(188.9, 'delay', 120)).toBeCloseTo(68.9)
  })

  it('never carries a completed practice frame into a formal trial', () => {
    const completedPracticeFrame={time:188.9,x:100,y:100} as Frame
    expect(simulationFrameForPhase('practice',completedPracticeFrame)).toBe(completedPracticeFrame)
    expect(simulationFrameForPhase('trial',completedPracticeFrame)).toBeUndefined()
  })

  it('defines the answer window separately from the frozen scenario timeline', () => {
    const html = renderToStaticMarkup(<FormalTaskInstructions busy={false} onContinue={()=>undefined}/>)
    expect(FORMAL_TRIAL_SECONDS).toBe(90)
    expect(RESULT_REVIEW_SECONDS).toBe(30)
    expect(formatCountdown(90)).toBe('01:30')
    expect(formatCountdown(1)).toBe('00:01')
    expect(html).toContain('页面顶部的倒计时只表示剩余作答时间')
    expect(html).toContain('不会推动场景中的风场时间')
  })
})

describe('practice one guided onboarding', () => {
  const emptySimulation={played:false,paused:false,resumed:false,switched:false,timerSeen:false}

  it('requires all three wind layers before advancing the layer step', () => {
    const incomplete=renderToStaticMarkup(<PracticeGuide step={4} openedLayers={[0,1]} simulationState={emptySimulation} mapInteracted={false} windInspected={false} hasInitialDecision={false} feedbackVisible={false} onNext={()=>undefined}/>)
    const complete=renderToStaticMarkup(<PracticeGuide step={4} openedLayers={[0,1,2]} simulationState={emptySimulation} mapInteracted={true} windInspected={false} hasInitialDecision={false} feedbackVisible={false} onNext={()=>undefined}/>)
    expect(incomplete).toContain('button disabled=""')
    expect(complete).not.toContain('button disabled=""')
    expect(complete).toContain('T0、T1、T2')
  })

  it('requires starting any simulation before completing the guide', () => {
    const beforePlay=renderToStaticMarkup(<PracticeGuide step={7} openedLayers={[0,1,2]} simulationState={emptySimulation} mapInteracted={true} windInspected={true} hasInitialDecision={false} feedbackVisible={false} onNext={()=>undefined}/>)
    const afterPlay=renderToStaticMarkup(<PracticeGuide step={7} openedLayers={[0,1,2]} simulationState={{...emptySimulation,played:true}} mapInteracted={true} windInspected={true} hasInitialDecision={false} feedbackVisible={false} onNext={()=>undefined}/>)
    expect(beforePlay).toContain('点击任一方案的仿真按钮')
    expect(beforePlay).toContain('button disabled=""')
    expect(afterPlay).not.toContain('button disabled=""')
    expect(afterPlay).not.toContain('正式实验的作答阶段没有仿真功能')
  })
})

describe('comprehension test', () => {
  it('contains the six requested questions and answer key', () => {
    expect(COMPREHENSION_QUESTIONS).toHaveLength(6)
    expect(Object.fromEntries(COMPREHENSION_QUESTIONS.map(question=>[question.id,question.answer]))).toEqual({
      wind:'B',layers:'C',delay:'A',goal:'B',flight_layers:'C',wind_effect:'B',
    })
    expect(COMPREHENSION_QUESTIONS[4].text).toContain('第120秒起飞')
    expect(COMPREHENSION_QUESTIONS[4].options).toContainEqual(['C','T1 和 T2'])
  })

  it('returns a specific rule reminder for every incorrect answer', () => {
    const feedback=comprehensionFeedback({wind:'A',layers:'C',delay:'B',goal:'A',flight_layers:'A',wind_effect:'C'})
    expect(feedback).toHaveLength(5)
    expect(feedback.join('\n')).toContain('实线箭头表示平均风向')
    expect(feedback.join('\n')).toContain('两种方案都沿 Path-A 飞行')
    expect(feedback.join('\n')).toContain('120 秒地面等待时间不计入调度目标')
    expect(feedback.join('\n')).toContain('经历 T1 和 T2')
    expect(feedback.join('\n')).toContain('顺风通常缩短飞行时间')
  })
})
