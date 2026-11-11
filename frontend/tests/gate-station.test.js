const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

test('gate station confirms matching OCR in one action and requires a reason for corrections', async () => {
  const calls = [];
  const nodes = {'#app': {innerHTML: ''}, '#toast': {textContent: '', className: ''}};
  const context = vm.createContext({
    window: {addEventListener() {}},
    document: {querySelector: key => nodes[key] || null},
    localStorage: {getItem: () => null},
    location: {hash: '', origin: 'http://localhost:5500'},
    setTimeout: () => 1, clearTimeout() {}, clearInterval() {},
    URL, console,
    calls,
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../js/app.js'), 'utf8'), context);
  vm.runInContext('api=async (path, options)=>{calls.push({path, options});if(path.endsWith("/prepare-exit"))return {status:"EXIT_READY"}}', context);

  const station = vm.runInContext('gateStation("IN", {id:7,session_code:"SES_7",card_uid:"CARD7",slot_id:"A1",status:"ENTRY_PENDING",entry_ocr_plate:"30A12345"}, null, {status:"RESERVED"}, {card_type:"MONTHLY",registered_plate:"30A12345"})', context);
  assert.match(station, /Xác nhận biển vào/);
  assert.match(station, /data-registered="30A12345"/);
  assert.doesNotMatch(station, /Lý do sửa\/duyệt biển<input[^>]*required/);

  const reasonInput = {value: '', required: false};
  const reason = {hidden: true, querySelector: () => reasonInput};
  const button = {disabled: false};
  const form = {
    dataset: {id: '7', lane: 'IN', ocr: '30A12345', entry: '', registered: '30A12345'},
    elements: {plate: {value: '30A12345'}, note: reasonInput},
    querySelector: selector => selector === '.station-reason' ? reason : button,
  };
  form.elements.plate.form = form;
  const event = {preventDefault() {}, currentTarget: form};
  await vm.runInContext('stationConfirm', context)(event);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].path, '/sessions/7/entry-plate');
  assert.match(calls[0].options.body.note, /đối chiếu ảnh/);
  assert.equal(reason.hidden, true);

  form.elements.plate.value = '30A12346';
  await vm.runInContext('stationConfirm', context)(event);
  assert.equal(calls.length, 1);
  assert.equal(reason.hidden, false);
  assert.equal(reasonInput.required, true);
  reasonInput.value = 'OCR đọc sai chữ số cuối';
  await vm.runInContext('stationConfirm', context)(event);
  assert.equal(calls.length, 2);
  assert.equal(calls[1].options.body.note, reasonInput.value);

  form.dataset.lane = 'OUT';
  form.dataset.ocr = '30A12345';
  form.dataset.entry = '30A12345';
  form.dataset.registered = '';
  form.elements.plate.value = '30A12345';
  reasonInput.value = '';
  await vm.runInContext('stationConfirm', context)(event);
  assert.equal(calls[2].path, '/sessions/7/resolve-plate');
  assert.equal(calls[2].options.body.actual_exit_plate, '30A12345');
  assert.match(calls[2].options.body.note, /đối chiếu ảnh/);

  form.dataset.ocr = '30A12346';
  form.elements.plate.value = '30A12345';
  await vm.runInContext('stationConfirm', context)(event);
  assert.equal(calls.length, 3);
  assert.equal(reason.hidden, false);

  await vm.runInContext('ACT.confirmEntry(7)', context);
  await vm.runInContext('ACT.confirmExit(7)', context);
  assert.equal(calls[3].path, '/sessions/7/confirm-entry');
  assert.equal(calls[4].path, '/sessions/7/confirm-exit');
  assert.equal(calls[3].options.body.confirmed_passage, true);

  form.dataset.status = 'ACTIVE';
  form.dataset.image = '/api/v1/alpr/images/demo_12345678.jpg';
  form.dataset.ocr = '30A12345';
  form.elements.plate.value = '30A12345';
  await vm.runInContext('stationConfirm', context)(event);
  assert.equal(calls[5].path, '/sessions/7/prepare-exit');
  assert.equal(calls[5].options.body.exit_image_url, form.dataset.image);
});
