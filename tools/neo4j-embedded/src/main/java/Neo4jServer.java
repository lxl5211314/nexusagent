import java.nio.file.Path;
import org.neo4j.configuration.GraphDatabaseSettings;
import org.neo4j.configuration.connectors.BoltConnector;
import org.neo4j.configuration.helpers.SocketAddress;
import org.neo4j.harness.Neo4j;
import org.neo4j.harness.Neo4jBuilders;

/**
 * Starts a real Neo4j instance (community, in-process) with the Bolt connector on
 * 7687, so the Python adapter talks the genuine wire protocol without Docker.
 *
 * <p>Usage: {@code java -cp "target/classes;target/cp.txt-contents" Neo4jServer [dataDir]}
 * <p>Prints {@code NEO4J_READY <bolt-uri>} once accepting connections.
 */
public final class Neo4jServer {

    private Neo4jServer() {
    }

    public static void main(String[] args) throws Exception {
        Path dataDir = Path.of(args.length > 0 ? args[0] : "target/neo4j-data");
        Neo4j neo4j = Neo4jBuilders.newInProcessBuilder()
                .withDisabledServer() // we only need Bolt, not the HTTP UI
                .withConfig(BoltConnector.enabled, true)
                .withConfig(BoltConnector.listen_address, new SocketAddress("127.0.0.1", 7687))
                .withConfig(BoltConnector.advertised_address, new SocketAddress("127.0.0.1", 7687))
                .withConfig(GraphDatabaseSettings.auth_enabled, false)
                .withConfig(GraphDatabaseSettings.initial_default_database, "neo4j")
                .withConfig(GraphDatabaseSettings.data_directory, dataDir.toAbsolutePath())
                .build();

        Runtime.getRuntime().addShutdownHook(new Thread(neo4j::close));
        System.out.println("NEO4J_READY " + neo4j.boltURI());
        System.out.flush();
        Thread.currentThread().join();
    }
}
