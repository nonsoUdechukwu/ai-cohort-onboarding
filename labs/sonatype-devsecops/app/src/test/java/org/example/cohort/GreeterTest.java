package org.example.cohort;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.Test;

class GreeterTest {
    @Test
    void mainPrintsInformalGreeting() {
        PrintStream original = System.out;
        var buffer = new ByteArrayOutputStream();
        try (var capture = new PrintStream(buffer, true, StandardCharsets.UTF_8)) {
            System.setOut(capture);
            Greeter.main(new String[0]);
        } finally {
            System.setOut(original);
        }
        assertEquals("Hello, cohort!", buffer.toString(StandardCharsets.UTF_8).strip());
    }
}
