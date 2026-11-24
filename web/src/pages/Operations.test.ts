import {describe,expect,it} from 'vitest';
import {trafficShares,type Port} from './Operations';
const port=(name:string,rx:number|null,tx:number|null,fresh=true):Port=>({index:name,name,status:'up',fresh,rx_bps:rx,tx_bps:tx});
describe('WAN usage shares from observed counters',()=>{
  it('excludes stale and unsupported links from the measured denominator',()=>{
    const result=trafficShares([port('WAN A',300,100),port('WAN B',500,300),port('Unsupported',null,null),port('Stale',9000,9000,false)]);
    expect(result.total).toBe(1200);expect(result.measured).toBe(2);
    expect(result.percentages[0].share).toBeCloseTo(100/3);expect(result.percentages[1].share).toBeCloseTo(200/3);
  });
  it('does not invent a filled usage chart for zero traffic or missing counters',()=>{
    expect(trafficShares([port('Idle',0,0)]).total).toBe(0);
    expect(trafficShares([port('Missing',null,null)]).measured).toBe(0);
  });
});
