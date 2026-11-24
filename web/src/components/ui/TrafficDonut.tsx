import {useEffect,useRef} from 'react';
import {init,use} from 'echarts/core';
import {PieChart} from 'echarts/charts';
import {TooltipComponent,AriaComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
use([PieChart,TooltipComponent,AriaComponent,CanvasRenderer]);
export default function TrafficDonut({data}:{data:Array<{name:string;value:number}>}) {
  const host=useRef<HTMLDivElement>(null);
  useEffect(()=>{
    if(!host.current)return;
    const chart=init(host.current);
    chart.setOption({animation:false,aria:{enabled:true},tooltip:{trigger:'item',renderMode:'richText',valueFormatter:(value:unknown)=>`${Number(value).toLocaleString()} bps`},
      series:[{type:'pie',radius:['52%','80%'],label:{show:false},data,emphasis:{scale:false}}]});
    const observer=new ResizeObserver(()=>chart.resize());observer.observe(host.current);
    return()=>{observer.disconnect();chart.dispose();};
  },[data]);
  return <div ref={host} style={{height:190}} role="img" aria-label="WAN connection share of measured receive and transmit traffic"/>;
}
