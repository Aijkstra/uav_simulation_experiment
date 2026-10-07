import type { Action, AssignedTrial, FormalSimulationResponse, ParticipantState, SimulationResponse, TrialDetail, TrialSummary } from './types'

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options)
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(body?.detail ?? `请求失败 (${response.status})`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  trials: () => request<TrialSummary[]>('/api/trials'),
  trial: (id: number) => request<TrialDetail>(`/api/trials/${id}`),
  simulate: (trialId: number, action: Action) => request<SimulationResponse>('/api/simulations', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ trialId, action }),
  }),
  event: (payload: Record<string, unknown>) => request<void>('/api/events', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }).catch(() => undefined),
  createParticipant: (participantId: string) => request<{participantId:string;orderId:number}>('/api/participants', {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({participantId}),
  }),
  participantState: (participantId:string) => request<ParticipantState>(`/api/participants/${participantId}/state`),
  nextTrial: (participantId:string) => request<AssignedTrial|{complete:true}>(`/api/participants/${participantId}/next-trial`),
  comprehension: (participantId:string, answers:Record<string,string>) => request<{correctCount:number;total:number;passed:boolean}>(`/api/participants/${participantId}/comprehension`, {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({answers}),
  }),
  practice: (practiceNo:number) => request<AssignedTrial&{practice:true}>(`/api/practice/${practiceNo}`),
  practiceSimulation: (practiceNo:number, action:Action) => request<SimulationResponse>(`/api/practice/${practiceNo}/simulation`, {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action}),
  }),
  formalSimulation: (participantId:string, trialId:number, action:Action) => request<FormalSimulationResponse>(`/api/participants/${participantId}/trials/${trialId}/simulation`, {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action}),
  }),
  submitPractice: (participantId:string, practiceNo:number, defaultPrediction:string, finalAction:Action) => request<{correct:boolean;bestAction:Action;feedback:string}>(`/api/participants/${participantId}/practice/${practiceNo}`, {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({defaultPrediction,finalAction}),
  }),
  submitTrial: (participantId:string, trialId:number, payload:Record<string,unknown>) => request<FormalSimulationResponse>(`/api/participants/${participantId}/trials/${trialId}/submit`, {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload),
  }),
  blockRating: (participantId:string, block:number, payload:Record<string,number>) => request<void>(`/api/participants/${participantId}/blocks/${block}/ratings`, {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload),
  }),
  complete: (participantId:string, questionnaire:Record<string,unknown>) => request<void>(`/api/participants/${participantId}/complete`, {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({questionnaire}),
  }),
}
