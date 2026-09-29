import { useEffect, useState } from 'react';
import { request } from './api';

type Preferences = {work_start:number;work_end:number;study_start:number|null;break_minutes:number;protected_titles:string[];preview_changes:boolean};

export function PreferencesForm() {
  const [value, setValue] = useState<Preferences|null>(null);
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    let active = true;
    request<Preferences>('/preferences').then(data => { if(active) setValue(data); })
      .catch(() => { if(active) setMessage('Could not load your preferences. Reopen Settings to try again.'); });
    return () => { active = false; };
  }, []);
  if(!value) return <p role="status">{message || 'Loading scheduling preferences…'}</p>;
  const update = (change:Partial<Preferences>) => {setValue({...value,...change});setMessage('');};
  return <form className="preferences-form" onSubmit={async event => {
    event.preventDefault(); setSaving(true); setMessage('');
    try { setValue(await request<Preferences>('/preferences',{method:'PUT',body:JSON.stringify(value)})); setMessage('Preferences saved.'); }
    catch(error) {setMessage(error instanceof Error ? error.message : 'Could not save preferences.');}
    finally {setSaving(false);}
  }}>
    <h2>How you like to plan</h2>
    <p>Used for free-time suggestions. All hours are in IST.</p>
    <label>Day starts at (24-hour clock)<input type="number" min={0} max={22} required value={value.work_start} onChange={e=>update({work_start:Number(e.target.value)})}/></label>
    <label>Day ends at (24-hour clock)<input type="number" min={1} max={23} required value={value.work_end} onChange={e=>update({work_end:Number(e.target.value)})}/></label>
    <label>Prefer study slots after (optional hour)<input type="number" min={0} max={22} value={value.study_start ?? ''} onChange={e=>update({study_start:e.target.value===''?null:Number(e.target.value)})}/></label>
    <label>Break between events (minutes)<input type="number" min={0} max={120} required value={value.break_minutes} onChange={e=>update({break_minutes:Number(e.target.value)})}/></label>
    <label>Protected events, one title per line<textarea rows={3} value={value.protected_titles.join('\n')} onChange={e=>update({protected_titles:e.target.value.split('\n')})}/></label>
    <small>The assistant will ask you to change this setting before moving or deleting a matching event.</small>
    <label className="check-preference"><input type="checkbox" checked={value.preview_changes} onChange={e=>update({preview_changes:e.target.checked})}/> Review single event creations and time moves before execution</label>
    <button className="primary" disabled={saving}>{saving?'Saving…':'Save preferences'}</button>
    <p role="status">{message}</p>
  </form>;
}
