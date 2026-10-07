export type Action = 'release' | 'delay' | 'reroute'

export interface TrialSummary {
  trialId: number
  block: number
  predictability?: string
  intervenability?: string
  droneCount: number
}

export interface Waypoint { waypoint_id: string; x: number; y: number }
export interface Route { pathId: string; taskRole: 'focused_candidate'; waypoints: Waypoint[] }
export interface WindZone {
  zoneId: string; cx: number; cy: number; radius: number
  meanSpeed: number; directionDeg: number; shapeSeed?: number
  speedSD: number; directionSD: number; isForecast: boolean
}
export interface WindLayer { layer: number; zones: WindZone[] }
export interface TrialDetail extends TrialSummary {
  delaySeconds: number
  defaultPredictionQuestion: string
  defaultPredictionOptions: string[]
  predictionReferenceAction: Action
  baselineTimeSeconds: number
  routeBaselineTimeSeconds: Record<string, number>
  noWindBenchmarks: Record<Action, NoWindBenchmark>
  intermediateQuestion: string
  intermediateOptions: string[]
  routes: Route[]
  windLayers: WindLayer[]
  windLayerStarts: number[]
  initialDrones?: DisplayDrone[]
}

export interface NoWindBenchmark {
  action: Action
  pathId: string
  startTimeSeconds: number
  flightTimeSeconds: number
  arrivalTimeSeconds: number
  zoneIntervals: Array<{
    zoneId: string
    entryTimeSeconds: number | null
    exitTimeSeconds: number | null
  }>
}

export interface AssignedTrial extends TrialDetail {
  complete: false
  positionInBlock: number
  globalOrder: number
}

export interface ParticipantState {
  participantId: string; orderId: number; status: string; completedTrials: number
  experimentVersion: string; materialVersion: string
}

export interface DisplayDrone { id: number; x: number; y: number }
export interface Frame {
  time: number; x: number; y: number; groundSpeed: number; batteryWh: number
  energyWh: number; eta: number; status: string; actualWindLayer: number
  segmentIndex: number
}
export interface SimulationResponse {
  sessionId: string; trialId: number; action: Action; pathId: string; seed: number
  summary: {
    totalTime: number; airborneTime: number; energyWh: number; minBatteryWh: number
    maxCrosswind: number; strongestZone: string | null
  }
  frames: Frame[]
}
export interface FormalSimulationResponse extends SimulationResponse {
  actionTimes: Record<Action, number>
  actualWindLayers: WindLayer[]
}
