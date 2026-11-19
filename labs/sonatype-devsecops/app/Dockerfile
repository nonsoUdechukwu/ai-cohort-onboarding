FROM eclipse-temurin:21-jre
WORKDIR /app
COPY target/released/app.jar app.jar
USER 10001:10001
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
