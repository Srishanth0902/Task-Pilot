import {test,expect} from '@playwright/test';

test.beforeEach(async({page})=>{
  await page.route('**/api/auth/me',r=>r.fulfill({json:{authenticated:true,user:{id:'alice',name:'Alice',email:'alice@example.com'}}}));
  await page.route('**/api/calendar/status',route=>route.fulfill({json:{guest:false,google_connected:true,active_provider:'google',active_provider_label:'Google Calendar',available_providers:[{name:'google',label:'Google Calendar',ready:true},{name:'native',label:'Task Pilot calendar',ready:true}]}}));
  await page.route('**/api/events?*',r=>r.fulfill({json:{success:true,events:[]}}));
  await page.route('**/api/health',r=>r.fulfill({json:{status:'ok',timezone:'Asia/Kolkata'}}));
  await page.route('**/api/conversations',r=>r.fulfill({json:[]}));
});

test('saves personal scheduling preferences',async({page})=>{
  let preferences={work_start:8,work_end:21,break_minutes:0,study_start:null,protected_titles:[],preview_changes:false};
  await page.route('**/api/preferences',async route=>{
    if(route.request().method()==='PUT') preferences=route.request().postDataJSON();
    await route.fulfill({json:preferences});
  });
  await page.goto('/');
  await page.getByRole('button',{name:'Settings',exact:true}).click();
  await page.getByLabel('Break between events (minutes)').fill('15');
  await page.getByLabel('Protected events, one title per line').fill('Yoga');
  await page.getByLabel('Review single event creations and time moves before execution').check();
  await page.getByRole('button',{name:'Save preferences'}).click();
  await expect(page.getByText('Preferences saved.')).toBeVisible();
  expect(preferences.break_minutes).toBe(15);
  expect(preferences.protected_titles).toEqual(['Yoga']);
  expect(preferences.preview_changes).toBe(true);
});

test('shows saved task and undo review',async({page})=>{
  const calls:string[]=[];
  await page.route('**/api/chat',async route=>{
    const {message,thread_id}=route.request().postDataJSON(); calls.push(message);
    const response=calls.length===1?'How long should Yoga last?':calls.length===2?'Created Yoga.':'Confirm undo?';
    await route.fulfill({json:{success:true,thread_id,response,requires_confirmation:calls.length===3,
      can_undo:calls.length===2, task_context:calls.length===1?{title:'Yoga',start_time:'2026-09-16T18:00:00+05:30',awaiting_duration:true}:null,
      events:[],conflicts:[],alternatives:[],proposed_changes:[]}});
  });
  await page.goto('/');
  await page.getByRole('textbox',{name:'Message your assistant'}).fill('Add Yoga tomorrow at 6 PM');
  await page.getByRole('button',{name:'Send message'}).click();
  await expect(page.getByText('Waiting for duration')).toBeVisible();
  await page.getByRole('textbox',{name:'Message your assistant'}).fill('45 minutes');
  await page.getByRole('button',{name:'Send message'}).click();
  await page.getByRole('button',{name:'Undo last change'}).click();
  await expect(page.getByRole('button',{name:'Confirm',exact:true})).toBeVisible();
  expect(calls).toEqual(['Add Yoga tomorrow at 6 PM','45 minutes','undo last change']);
});
