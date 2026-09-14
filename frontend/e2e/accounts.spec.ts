import {test,expect} from '@playwright/test';

test('signed-out visitor sees Google login and does not fetch events',async({page})=>{
  let eventRequests=0;
  await page.route('**/api/auth/me',route=>route.fulfill({json:{authenticated:false,login_configured:true,user:null}}));
  await page.route('**/api/events?*',route=>{eventRequests++;return route.fulfill({json:{events:[]}});});
  await page.goto('/');
  await expect(page.getByRole('link',{name:'Choose a Google account'})).toHaveAttribute('href','/api/auth/login');
  expect(eventRequests).toBe(0);
});

test('signed-in user can sign out or switch Google accounts',async({page})=>{
  await page.route('**/api/auth/me',route=>route.fulfill({json:{authenticated:true,login_configured:true,user:{id:'alice',name:'Alice',email:'alice@example.com'}}}));
  await page.route('**/api/events?*',route=>route.fulfill({json:{success:true,events:[]}}));
  await page.route('**/api/health',route=>route.fulfill({json:{status:'ok',timezone:'Asia/Kolkata'}}));
  await page.route('**/api/conversations',route=>route.fulfill({json:[]}));
  let logoutCalls=0;
  await page.route('**/api/auth/logout',route=>{logoutCalls++;return route.fulfill({json:{success:true}});});
  await page.goto('/');
  await page.getByRole('button',{name:'Google account: alice@example.com'}).click();
  await expect(page.getByRole('menuitem',{name:'Switch account'})).toBeVisible();
  await expect(page.getByRole('menuitem',{name:'Sign out'})).toBeVisible();
  expect(logoutCalls).toBe(0);
});

test('saved confirmation can be reopened after page reload',async({page})=>{
  await page.route('**/api/auth/me',route=>route.fulfill({json:{authenticated:true,user:{id:'alice',email:'alice@example.com',name:'Alice'}}}));
  await page.route('**/api/events?*',route=>route.fulfill({json:{events:[]}}));
  await page.route('**/api/health',route=>route.fulfill({json:{status:'ok',timezone:'Asia/Kolkata'}}));
  await page.route('**/api/conversations',route=>route.fulfill({json:[{id:'saved',title:'Delete Yoga',updated:1700000000}]}));
  await page.route('**/api/conversations/saved',route=>route.fulfill({json:{messages:[{role:'user',text:'Delete Yoga'},{role:'assistant',text:'Delete Yoga?'}],latest:{response:'Delete Yoga?',requires_confirmation:true,proposed_changes:[],alternatives:[],conflicts:[],events:[]}}}));
  await page.goto('/');
  await page.reload();
  await page.getByRole('button',{name:/Delete Yoga/}).click();
  await expect(page.getByText('Delete Yoga?',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Confirm',exact:true})).toBeVisible();
});
