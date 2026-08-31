package switcher

import "testing"

func TestPlanTH413IsDiscreteAndOrdered(t *testing.T) {
	got, err := Plan(TH413, 3)
	if err != nil {
		t.Fatal(err)
	}
	want := []Event{{"ControlRight", "down"}, {"ControlRight", "up"}, {"ControlRight", "down"}, {"ControlRight", "up"}, {"Digit3", "down"}, {"Digit3", "up"}, {"Enter", "down"}, {"Enter", "up"}}
	if len(got) != len(want) {
		t.Fatalf("got %d events", len(got))
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("event %d: got %+v want %+v", i, got[i], want[i])
		}
	}
}

func TestPlanRejectsOutOfRangePort(t *testing.T) {
	if _, err := Plan(TH413, 5); err == nil {
		t.Fatal("expected port validation error")
	}
}
